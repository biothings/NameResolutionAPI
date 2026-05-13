from nameres.handlers.lookup import LookupQuery, _build_elasticsearch_query


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
