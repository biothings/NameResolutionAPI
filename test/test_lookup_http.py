import dataclasses
import json
from urllib.parse import urlencode

from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application

from nameres.handlers.lookup import (
    NameResolutionBulkLookupHandler,
    NameResolutionLookupHandler,
    _build_elasticsearch_query,
)


class EchoLookupHandler(NameResolutionLookupHandler):
    """Expose the generated query without requiring an Elasticsearch connection."""

    async def get(self):
        lookup_query = self.lookup_queries[0]
        self.finish_json(
            {
                "lookup_query": dataclasses.asdict(lookup_query),
                "elasticsearch_query": _build_elasticsearch_query(lookup_query, self.filters),
            }
        )


class EchoBulkLookupHandler(NameResolutionBulkLookupHandler):
    """Expose prepared arguments without requiring an Elasticsearch connection."""

    async def post(self):
        self.finish_json(
            {
                "lookup_query": dataclasses.asdict(self.lookup_queries[0]),
                "filters": self.filters,
            }
        )


class TestBulkLookupRequestParsing(AsyncHTTPTestCase):
    def get_app(self):
        return Application([(r"/bulk-lookup", EchoBulkLookupHandler)])

    def test_json_body_options_override_query_arguments(self):
        response = self.fetch(
            "/bulk-lookup?limit=0&only_prefixes=NCBIGene",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps(
                {
                    "strings": ["aspirin"],
                    "limit": 1,
                    "only_prefixes": "CHEBI",
                }
            ),
        )

        assert response.code == 200
        body = json.loads(response.body)
        assert body["lookup_query"]["limit"] == 1
        assert body["filters"]["filter"] == [
            {
                "bool": {
                    "should": [{"prefix": {"curie": "CHEBI"}}],
                    "minimum_should_match": 1,
                }
            }
        ]

    def test_malformed_json_is_a_validation_error(self):
        response = self.fetch(
            "/bulk-lookup",
            method="POST",
            headers={"Content-Type": "application/json"},
            body="{",
        )

        assert response.code == 422
        assert response.headers["Content-Type"] == "application/json; charset=UTF-8"
        assert json.loads(response.body) == {
            "detail": [
                {
                    "loc": ["request"],
                    "msg": "Lookup request body must be valid JSON",
                    "type": "value_error",
                }
            ]
        }


class TestLookupRequestParsing(AsyncHTTPTestCase):
    def get_app(self):
        return Application([(r"/lookup", EchoLookupHandler)])

    def test_biomedical_names_reach_elasticsearch_queries_without_lucene_escaping(self):
        names = {
            "BRCA1": "brca1",
            "TP53": "tp53",
            "CYP2D6": "cyp2d6",
            "IL-6": "il-6",
            "SARS-CoV-2": "sars-cov-2",
            "BCR::ABL1": "bcr::abl1",
        }

        for raw_string, normalized_string in names.items():
            with self.subTest(raw_string=raw_string):
                response = self.fetch(
                    f"/lookup?{urlencode({'string': raw_string, 'autocomplete': 'false', 'limit': 3})}"
                )

                assert response.code == 200
                body = json.loads(response.body)
                assert body["lookup_query"]["raw_string"] == raw_string
                assert body["lookup_query"]["query_strings"] == [normalized_string]

                clauses = body["elasticsearch_query"]["bool"]["must"][0]["dis_max"]["queries"]
                assert [clause["multi_match"]["query"] for clause in clauses] == [
                    normalized_string,
                    normalized_string,
                ]
                assert [clause["multi_match"]["type"] for clause in clauses] == ["phrase", "best_fields"]

    def test_biomedical_autocomplete_uses_the_same_unescaped_normalized_string(self):
        response = self.fetch(
            f"/lookup?{urlencode({'string': 'SARS-CoV-2', 'autocomplete': 'true', 'limit': 3})}"
        )

        assert response.code == 200
        body = json.loads(response.body)
        assert body["lookup_query"]["query_strings"] == ["sars-cov-2"]

        clauses = body["elasticsearch_query"]["bool"]["must"][0]["dis_max"]["queries"]
        assert [clause["multi_match"]["query"] for clause in clauses] == ["sars-cov-2"] * 3
        assert [clause["multi_match"]["type"] for clause in clauses] == [
            "phrase",
            "best_fields",
            "phrase_prefix",
        ]
