from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from nameres.handlers.lookup import (
    BaseNameResolutionLookupHandler,
    LookupQuery,
    _build_elasticsearch_query,
    lookup,
)


def test_biolink_type_filters_accept_singular_and_plural_arguments():
    handler = Mock()
    query_arguments = {
        "biolink_types": ["biolink:Disease", " Gene "],
        "biolink_type": ["biolink:PhenotypicFeature", "  "],
    }
    handler.get_arguments.side_effect = lambda name: query_arguments.get(name, [])
    handler.get_argument.side_effect = lambda _name, default, strip: default

    filters = BaseNameResolutionLookupHandler._build_lookup_filters(handler)

    assert filters == {
        "should": [
            {"term": {"biolink_types": "Disease"}},
            {"term": {"biolink_types": "Gene"}},
            {"term": {"biolink_types": "PhenotypicFeature"}},
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

    results = await lookup(metadata, lookup_query, {"should": [], "must_not": []})

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

    query = _build_elasticsearch_query(lookup_query, {"should": [], "must_not": []})

    dis_max_queries = query["bool"]["must"][0]["dis_max"]["queries"]
    assert dis_max_queries == [
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


def test_non_autocomplete_query_does_not_add_prefix_match():
    lookup_query = LookupQuery(
        raw_string="diabe",
        query_strings=("diabe",),
        autocomplete=False,
        highlighting=False,
        offset=0,
        limit=10,
    )

    query = _build_elasticsearch_query(lookup_query, {"should": [], "must_not": []})

    assert query["bool"]["must"][0]["dis_max"]["queries"] == [
        {
            "multi_match": {
                "query": "diabe",
                "type": "best_fields",
                "fields": ["preferred_name^25", "names^10"],
            }
        }
    ]
