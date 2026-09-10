import dataclasses
import json

from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application

from nameres.handlers.lookup import NameResolutionBulkLookupHandler


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
