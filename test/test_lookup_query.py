import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from nameres.handlers.lookup import (
    BaseNameResolutionLookupHandler,
    LookupArgumentException,
    LookupQuery,
    NameResolutionBulkLookupHandler,
    _build_elasticsearch_query,
    lookup,
)


def _make_handler(handler_class=BaseNameResolutionLookupHandler, *, body=None, query_arguments=None):
    """Build a handler without starting Tornado so argument parsing stays unit-testable."""
    handler = object.__new__(handler_class)
    handler.request = SimpleNamespace(
        method="POST",
        body=json.dumps(body).encode("utf-8") if body is not None else b"",
    )
    handler.json_body_arguments = {}
    query_arguments = query_arguments or {}

    def get_argument(name, default=None, strip=True):
        arguments = query_arguments.get(name, [])
        if not arguments:
            return default
        argument = arguments[-1]
        return argument.strip() if strip and isinstance(argument, str) else argument

    def get_arguments(name, strip=True):
        arguments = query_arguments.get(name, [])
        return [argument.strip() if strip and isinstance(argument, str) else argument for argument in arguments]

    handler.get_argument = Mock(side_effect=get_argument)
    handler.get_arguments = Mock(side_effect=get_arguments)
    return handler


@pytest.mark.parametrize(
    ("raw_string", "normalized_string"),
    [
        ("BRCA1", "brca1"),
        ("TP53", "tp53"),
        ("CYP2D6", "cyp2d6"),
        ("IL-6", "il-6"),
        ("SARS-CoV-2", "sars-cov-2"),
        ("BCR::ABL1", "bcr::abl1"),
    ],
)
def test_sanitize_preserves_biomedical_names_without_lucene_escaping(raw_string, normalized_string):
    handler = _make_handler()

    assert handler._sanitize_lookup_query([raw_string]) == [(raw_string, (normalized_string,))]


def test_prepare_and_query_builder_keep_brca1_intact():
    handler = _make_handler(query_arguments={"string": [" BRCA1 "], "autocomplete": ["false"]})

    BaseNameResolutionLookupHandler.prepare(handler)

    assert handler.lookup_queries == [
        LookupQuery(
            raw_string="BRCA1",
            query_strings=("brca1",),
            autocomplete=False,
            highlighting=False,
            offset=0,
            limit=10,
        )
    ]
    query = _build_elasticsearch_query(handler.lookup_queries[0], handler.filters)
    query_clauses = query["bool"]["must"][0]["dis_max"]["queries"]
    assert [clause["multi_match"]["query"] for clause in query_clauses] == ["brca1", "brca1"]
    assert [clause["multi_match"]["type"] for clause in query_clauses] == ["phrase", "best_fields"]


def test_biolink_type_filters_accept_singular_and_plural_arguments():
    handler = _make_handler(
        query_arguments={
            "biolink_types": ["biolink:Disease", " Gene "],
            "biolink_type": ["biolink:PhenotypicFeature", "  "],
        }
    )

    filters = BaseNameResolutionLookupHandler._build_lookup_filters(handler)

    assert filters == {
        "filter": [
            {
                "bool": {
                    "should": [
                        {"term": {"biolink_types": "Disease"}},
                        {"term": {"biolink_types": "Gene"}},
                        {"term": {"biolink_types": "PhenotypicFeature"}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        ],
        "must_not": [],
    }


async def test_lookup_requests_and_returns_preferred_name_highlights():
    search = AsyncMock(
        return_value={
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "curie": "MONDO:0005015",
                            "preferred_name": "diabetes mellitus",
                            "names": ["diabetes", "diabetes mellitus"],
                            "taxa": [],
                            "biolink_types": ["Disease"],
                            "clique_identifier_count": 1,
                        },
                        "_score": 42.0,
                        "highlight": {
                            "preferred_name": ["<strong>diabetes</strong> mellitus"],
                            "names": ["<strong>diabetes</strong>"],
                        },
                    }
                ]
            }
        }
    )
    metadata = SimpleNamespace(
        elasticsearch=SimpleNamespace(async_client=SimpleNamespace(search=search), indices=["nameres"])
    )
    lookup_query = LookupQuery(
        raw_string="diabetes",
        query_strings=("diabetes",),
        autocomplete=False,
        highlighting=True,
        offset=0,
        limit=10,
    )

    results = await lookup(metadata, lookup_query, {"filter": [], "must_not": []})

    assert search.await_args.kwargs["highlight"]["fields"] == {
        "names": {"pre_tags": ["<strong>"], "post_tags": ["</strong>"]},
        "preferred_name": {"pre_tags": ["<strong>"], "post_tags": ["</strong>"]},
    }
    assert results[0]["highlighting"] == {
        "labels": ["<strong>diabetes</strong> mellitus"],
        "synonyms": ["<strong>diabetes</strong>"],
    }


def test_autocomplete_query_treats_final_term_as_prefix():
    lookup_query = LookupQuery(
        raw_string="diabe",
        query_strings=("diabe",),
        autocomplete=True,
        highlighting=False,
        offset=0,
        limit=10,
    )

    query = _build_elasticsearch_query(lookup_query, {"filter": [], "must_not": []})

    dis_max_queries = query["bool"]["must"][0]["dis_max"]["queries"]
    assert dis_max_queries == [
        {
            "multi_match": {
                "query": "diabe",
                "type": "phrase",
                "fields": ["preferred_name^30", "names^20"],
            }
        },
        {
            "multi_match": {
                "query": "diabe",
                "type": "best_fields",
                "fields": ["preferred_name^25", "names^10"],
            }
        },
        {
            "multi_match": {
                "query": "diabe",
                "type": "phrase_prefix",
                "fields": ["preferred_name^30", "names^20"],
            }
        },
    ]


def test_non_autocomplete_query_adds_phrase_and_token_matches_without_prefix_match():
    lookup_query = LookupQuery(
        raw_string="diabe",
        query_strings=("diabe",),
        autocomplete=False,
        highlighting=False,
        offset=0,
        limit=10,
    )

    query = _build_elasticsearch_query(lookup_query, {"filter": [], "must_not": []})

    assert query["bool"]["must"][0]["dis_max"]["queries"] == [
        {
            "multi_match": {
                "query": "diabe",
                "type": "phrase",
                "fields": ["preferred_name^30", "names^20"],
            }
        },
        {
            "multi_match": {
                "query": "diabe",
                "type": "best_fields",
                "fields": ["preferred_name^25", "names^10"],
            }
        },
    ]


def test_each_filter_category_becomes_its_own_group():
    handler = _make_handler(
        query_arguments={
            "biolink_type": ["biolink:Disease"],
            "only_prefixes": ["MONDO| |HP"],
            "only_taxa": [" NCBITaxon:9606 "],
        }
    )

    filters = BaseNameResolutionLookupHandler._build_lookup_filters(handler)

    # One group per supplied category. Clauses are OR'd within a group; the groups
    # themselves are AND'd against each other by _build_elasticsearch_query.
    assert filters == {
        "filter": [
            {
                "bool": {
                    "should": [{"term": {"biolink_types": "Disease"}}],
                    "minimum_should_match": 1,
                }
            },
            {
                "bool": {
                    "should": [{"prefix": {"curie": "MONDO"}}, {"prefix": {"curie": "HP"}}],
                    "minimum_should_match": 1,
                }
            },
            {
                "bool": {
                    "should": [{"term": {"taxa": "NCBITaxon:9606"}}],
                    "minimum_should_match": 1,
                }
            },
        ],
        "must_not": [],
    }


def test_omitted_filter_categories_contribute_no_group():
    handler = _make_handler()

    filters = BaseNameResolutionLookupHandler._build_lookup_filters(handler)

    assert filters == {"filter": [], "must_not": []}


def test_bulk_json_body_supplies_all_lookup_options_and_overrides_query_arguments():
    handler = _make_handler(
        NameResolutionBulkLookupHandler,
        body={
            "strings": [" Diabetes "],
            "autocomplete": True,
            "highlighting": True,
            "offset": 2,
            "limit": 3,
            "biolink_types": ["biolink:Disease", "PhenotypicFeature"],
            "only_prefixes": "MONDO|HP",
            "exclude_prefixes": "UMLS",
            "only_taxa": "NCBITaxon:9606",
        },
        query_arguments={
            "autocomplete": ["false"],
            "highlighting": ["false"],
            "offset": ["20"],
            "limit": ["30"],
            "biolink_types": ["Gene"],
            "biolink_type": ["Protein"],
            "only_prefixes": ["NCBIGene"],
            "exclude_prefixes": ["MONDO"],
            "only_taxa": ["NCBITaxon:10090"],
        },
    )

    BaseNameResolutionLookupHandler.prepare(handler)

    assert handler.lookup_queries == [
        LookupQuery(
            raw_string="Diabetes",
            query_strings=("diabetes",),
            autocomplete=True,
            highlighting=True,
            offset=2,
            limit=3,
        )
    ]
    assert handler.filters == {
        "filter": [
            {
                "bool": {
                    "should": [
                        {"term": {"biolink_types": "Disease"}},
                        {"term": {"biolink_types": "PhenotypicFeature"}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            {
                "bool": {
                    "should": [{"prefix": {"curie": "MONDO"}}, {"prefix": {"curie": "HP"}}],
                    "minimum_should_match": 1,
                }
            },
            {
                "bool": {
                    "should": [{"term": {"taxa": "NCBITaxon:9606"}}],
                    "minimum_should_match": 1,
                }
            },
        ],
        "must_not": [{"prefix": {"curie": "UMLS"}}],
    }


def test_bulk_json_body_uses_query_arguments_as_a_backward_compatible_fallback():
    handler = _make_handler(
        NameResolutionBulkLookupHandler,
        body={"strings": ["aspirin"]},
        query_arguments={
            "autocomplete": ["true"],
            "limit": ["1"],
            "biolink_type": ["Drug"],
            "only_prefixes": ["CHEBI"],
        },
    )

    BaseNameResolutionLookupHandler.prepare(handler)

    assert handler.lookup_queries[0].autocomplete is True
    assert handler.lookup_queries[0].limit == 1
    assert handler.filters["filter"] == [
        {
            "bool": {
                "should": [{"term": {"biolink_types": "Drug"}}],
                "minimum_should_match": 1,
            }
        },
        {
            "bool": {
                "should": [{"prefix": {"curie": "CHEBI"}}],
                "minimum_should_match": 1,
            }
        },
    ]


def test_falsey_bulk_json_values_still_override_query_arguments():
    handler = _make_handler(
        NameResolutionBulkLookupHandler,
        body={
            "strings": ["aspirin"],
            "autocomplete": False,
            "highlighting": None,
            "offset": 0,
            "limit": 0,
            "biolink_types": [],
            "only_prefixes": "",
            "exclude_prefixes": None,
        },
        query_arguments={
            "autocomplete": ["true"],
            "highlighting": ["true"],
            "offset": ["4"],
            "limit": ["5"],
            "biolink_type": ["Drug"],
            "only_prefixes": ["CHEBI"],
            "exclude_prefixes": ["UMLS"],
        },
    )

    BaseNameResolutionLookupHandler.prepare(handler)

    assert handler.lookup_queries[0].autocomplete is False
    assert handler.lookup_queries[0].highlighting is False
    assert handler.lookup_queries[0].offset == 0
    assert handler.lookup_queries[0].limit == 0
    assert handler.filters == {"filter": [], "must_not": []}


@pytest.mark.parametrize(
    "body",
    [
        ["aspirin"],
        {"strings": "aspirin"},
        {"strings": None},
        {"strings": ["aspirin", 42]},
        {"strings": ["aspirin"], "biolink_types": "Drug"},
        {"strings": ["aspirin"], "only_prefixes": ["CHEBI"]},
        {"strings": ["aspirin"], "autocomplete": "true"},
        {"strings": ["aspirin"], "offset": True},
        {"strings": ["aspirin"], "limit": 1.9},
    ],
)
def test_bulk_json_body_rejects_invalid_shapes(body):
    handler = _make_handler(NameResolutionBulkLookupHandler, body=body)

    with pytest.raises(LookupArgumentException):
        BaseNameResolutionLookupHandler.prepare(handler)


def test_bulk_json_body_rejects_malformed_json():
    handler = _make_handler(NameResolutionBulkLookupHandler)
    handler.request.body = b"{"

    with pytest.raises(LookupArgumentException, match="valid JSON"):
        BaseNameResolutionLookupHandler.prepare(handler)


def test_filter_groups_are_added_without_replacing_the_search_query():
    lookup_query = LookupQuery(
        raw_string="insulin",
        query_strings=("insulin",),
        autocomplete=False,
        highlighting=False,
        offset=0,
        limit=10,
    )
    filters = {
        "filter": [
            {
                "bool": {
                    "should": [{"term": {"biolink_types": "Disease"}}],
                    "minimum_should_match": 1,
                }
            },
            {
                "bool": {
                    "should": [{"prefix": {"curie": "MONDO"}}, {"prefix": {"curie": "HP"}}],
                    "minimum_should_match": 1,
                }
            },
        ],
        "must_not": [{"prefix": {"curie": "UMLS"}}],
    }

    query = _build_elasticsearch_query(lookup_query, filters)

    # The name-matching dis_max remains the only scoring must clause.
    assert len(query["bool"]["must"]) == 1
    assert "dis_max" in query["bool"]["must"][0]
    # Each category stays a separate required bool under non-scoring filter context.
    assert query["bool"]["filter"] == filters["filter"]
    assert query["bool"]["must_not"] == [{"prefix": {"curie": "UMLS"}}]


def test_exclude_prefixes_populate_must_not_without_any_filter_group():
    lookup_query = LookupQuery(
        raw_string="insulin",
        query_strings=("insulin",),
        autocomplete=False,
        highlighting=False,
        offset=0,
        limit=10,
    )

    query = _build_elasticsearch_query(lookup_query, {"filter": [], "must_not": [{"prefix": {"curie": "UMLS"}}]})

    assert len(query["bool"]["must"]) == 1
    assert "dis_max" in query["bool"]["must"][0]
    assert "filter" not in query["bool"]
    assert query["bool"]["must_not"] == [{"prefix": {"curie": "UMLS"}}]


def test_unfiltered_query_adds_no_filter_clauses():
    lookup_query = LookupQuery(
        raw_string="insulin",
        query_strings=("insulin",),
        autocomplete=False,
        highlighting=False,
        offset=0,
        limit=10,
    )

    query = _build_elasticsearch_query(lookup_query, {"filter": [], "must_not": []})

    assert len(query["bool"]["must"]) == 1
    assert "dis_max" in query["bool"]["must"][0]
    assert "filter" not in query["bool"]
    assert "must_not" not in query["bool"]
