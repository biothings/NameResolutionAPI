#### nameres lookup overview

The primary endpoint provided by nameres is the `lookup` endpoint.
This allows for users to provide a search term or phrase / a group of search terms or phrases
search for within our elasticsearch index. The previous implementation utilized [apache
solr](https://solr.apache.org/). Within the core-components group, the recent transistion of
NodeNorm to elasticsearch has also prompted us to see if we can consolidate most of the core
services to leverage the same backend to simplify how we store all of our node and annotation data.

* NameResolution
    * Repository: https://github.com/NCATSTranslator/NameResolution
    * API: https://name-resolution-sri.renci.org/docs



#### elasticsearch lookup query

The elasticsearch implementation effectively copied the singular main method leveraged in the solr
fastapi version. The compatibility target for the recent filter work is the deployed Solr
[`v1.5.2` implementation](https://github.com/NCATSTranslator/NameResolution/blob/9788e6f618814357f9e2b27e74fd9cc4dbb1d694/api/server.py#L517-L590).

`/lookup` takes its arguments from URL query parameters. The advertised `/bulk-lookup` contract takes
a JSON object with a required `strings` array and the same lookup options. In this implementation, a
bulk option in the JSON body takes precedence; an option omitted from the body may fall back to its
URL query parameter.

```shell
Argument Matrix:
| argument_name    | type      | required | default |
| string           | str       | True     | None    | < lookup GET|POST
| strings          | list[str] | True     | None    | < bulklookup POST
| autocomplete     | bool      | False    | False   |
| highlighting     | bool      | False    | False   |
| offset           | int       | False    | 0       |
| limit            | int       | False    | 10      |
| biolink_type     | list[str] | False    | []      |
| only_prefixes    | str       | False    | None    |
| exclude_prefixes | str       | False    | None    |
| only_taxa        | str       | False    | None    |

Descriptions

string: The string to search for. Only required argument. Used

autocomplete: Toggle autocomplete on the search term.
If autocomplete is enabled, we assume the input string is an in-complete
phrase, whereas autocomplete disabled assumes that the search term is a complete phrase

highlighting: Toggle return information on which labels
and synonyms matched the search query

offset: The number of results to skip.
Offset must be greater than or equal to 0 (cannot have a negative offset). Primarily
used for result pagination

limit: The number of results to return.
Limit must be in the range [0, 1000]. Primarily used for result pagination
(This is the legacy/OpenAPI contract; the Elasticsearch handler currently checks only that it is non-negative.)

biolink_types: The Biolink types to filter to (with or without the `biolink:` prefix).
Examples: <["biolink:Disease", "biolink:PhenotypicFeature"]>, would apply
filtering for the types `biolink:Disease` OR `biolink:PhenotypicFeature`.
Results with either would result in a match. `/lookup` uses the repeatable singular
`biolink_type` parameter (and accepts `biolink_types` as an alias); bulk JSON uses `biolink_types`.

only_prefixes: Pipe-separated, case-sensitive list of prefixes to filter.
Examples: <"MONDO|EFO">, would apply filters for `MONDO` OR `EFO`

exclude_prefixes: Pipe-separated, case-sensitive list of prefixes to exclude.
Examples: <"UMLS|EFO"> would apply filters for `UMLS` or `EFO`

only_taxa: Pipe-separated, case-sensitive list of taxa to filter.
Examples: <"NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955">
would apply taxa filters for each pipe separated entry
```


##### search phrase sanitization
The lookup endpoint uses structured Elasticsearch Query DSL rather than a Lucene query-string
parser. Sanitization therefore does not add backslashes or otherwise escape punctuation. It
trims and lowercases each input, normalizes Windows smart single and double quotes, and omits
empty searches. Each non-empty input produces one normalized query string. For example, `BRCA1`
becomes `brca1`, while punctuation in `IL-6`, `SARS-CoV-2`, and `BCR::ABL1` is preserved for
the configured field analyzers.

Sanitization operations:

1) Strip and lowercase the query (all indexes are case-insensitive).
2) Normalize Windows smart quotes.
    There is a possibility that the input text isn't in UTF-8.
    Python packages that try to determine what the encoding is:
    - https://pypi.org/project/charset-normalizer/
    - https://www.crummy.com/software/BeautifulSoup/bs4/doc/#unicode-dammit
    But the only issue we've run into so far has been the Windows smart
    quote (https://github.com/TranslatorSRI/NameResolution/issues/176), so
    let's detect and replace just those characters.
3) Prune empty searches.
4) Preserve all other characters for the Elasticsearch field analyzers.

The handler returns `(raw_string, (normalized_string,))` for each non-empty input; the normalized
string is the only value sent to Elasticsearch.

##### filters

We have 4 different filters we have to apply to our query depending on what the user
supplies

Values within one positive filter category are combined with OR. Separately supplied categories
are combined with AND. This matches the deployed Solr implementation, which sends each category as
a separate filter. These constraints restrict which documents can match but do not affect their
relevance scores.

1) biolink-type
If the user supplies a biolink-type or a collection of biolink-types, we have to apply a filter
to the search to only include results which match the specification. The query by itself is a simple
`term` based filter within a `should` clause for each biolink-type specified

```JSON
{
    "should": [
        {
            "term": {"biolink_types": <biolink_type0>}
        },
        {
            "term": {"biolink_types": <biolink_type1>}
        },
        ...
        {
            "term": {"biolink_types": <biolink_typeN>}
        },
    ]
}

2) only-prefixes
Same as case 1, but in this case looking for filtering by specified CURIE prefix. Still leveraged
in a `should` clause, but leverages `prefix` instead of `term`

```JSON
{
    "should": [
        {
            "prefix": {"curie": <curie_prefix0>}
        },
        {
            "prefix": {"curie": <curie_prefix1>}
        },
        ...
        {
            "prefix": {"curie": <curie_prefixN>}
        },
    ]
}

3) exclude-prefixes
The inversion of case 2, this filters curie prefixes that we don't want included in the final
results. Leverages a `must_not` clause with the `prefix` query

```JSON
{
    "must_not": [
        {
            "prefix": {"curie": <curie_prefix0>}
        },
        {
            "prefix": {"curie": <curie_prefix1>}
        },
        ...
        {
            "prefix": {"curie": <curie_prefixN>}
        },
    ]
}
```

4) only-taxa
Same as case 1, but in this case looking for filtering by specified taxon. Still leveraged
in a `should` clause, along with the same `term` query

```JSON
{
    "should": [
        {
            "term": {"taxa": <taxon0>}
        },
        {
            "term": {"taxa": <taxon1>}
        },
        ...
        {
            "term": {"taxa": <taxonN>}
        },
    ]
}
```

The Elasticsearch syntax differs from Solr, but the intended semantics are the same. Elasticsearch
uses `bool.filter` for the positive category groups and `bool.must_not` for exclusions. Solr sends a
list through its `filter` request property. Both forms AND the separately supplied categories and
keep them out of relevance scoring.


```python
def _build_lookup_filters(self) -> dict:
    """Build non-scoring Elasticsearch filters for lookup requests.

    Values within a positive filter category are combined with OR, while
    separate categories are combined with AND. Excluded prefixes are
    represented as ``must_not`` clauses.
    """

    # The singular spelling remains supported as a URL-query compatibility alias.
    biolink_types = self._get_lookup_arguments("biolink_types", "biolink_type")
    only_prefixes = self._get_pipe_delimited_lookup_argument("only_prefixes")
    exclude_prefixes = self._get_pipe_delimited_lookup_argument("exclude_prefixes")
    only_taxa = self._get_pipe_delimited_lookup_argument("only_taxa")

    # Apply filters as needed.
    es_filters = {"filter": [], "must_not": []}

    # OR-relationship within each group, chained with AND-relationship between groups.
    for values, build in [
        (biolink_types, lambda v: {"term": {"biolink_types": v.removeprefix("biolink:")}}),
        (only_prefixes, lambda v: {"prefix": {"curie": v}}),
        (only_taxa, lambda v: {"term": {"taxa": v}}),
    ]:
        should_filters = [build(s) for v in values if (s := v.strip())]
        if should_filters:
            es_filters["filter"].append(
                {"bool": {"should": should_filters, "minimum_should_match": 1}}
            )

    # Prefix: exclude filter
    # Elasticsearch must not
    for prefix in exclude_prefixes:
        prefix = prefix.strip()
        must_not_filter = {"prefix": {"curie": prefix}}
        es_filters["must_not"].append(must_not_filter)

    # We also need to include entries that don't have taxa specified.
    # TODO Skipping for the moment as we need to update the index
    # { "term" : { "taxon_specific" : False } }

    return es_filters
```


##### build elasticsearch query

So this query is fairly complicated because we have a lot of specifications we want to achieve from
our lookup. The overall structure of the query is the following:

```jsonc
{
    "bool": {
        "must": [
            {
                "dis_max": {
                    "queries": [
                        {
                            "multi_match": {
                                "query": normalized_lookup_string,
                                "type": "phrase",
                                "fields": ["preferred_name^30", "names^20"]
                            }
                        },
                        {
                            "multi_match": {
                                "query": normalized_lookup_string,
                                "type": "best_fields",
                                "fields": ["preferred_name^25", "names^10"]
                            }
                        },

                        // Included only when autocomplete is enabled
                        {
                            "multi_match": {
                                "query": normalized_lookup_string,
                                "type": "phrase_prefix",
                                "fields": ["preferred_name^30", "names^20"]
                            }
                        }
                    ]
                }
            }
        ],
        "filter": [
            {
                "bool": {
                    "should": [<insert filters for one positive category>],
                    "minimum_should_match": 1
                }
            }
        ],
        "must_not": [<insert excluded-prefix filters>]
    }
}
```

The `dis_max` (disjunction max) query returns documents that match one or more of its alternatives
and uses the highest-scoring match as the score. No `tie_breaker` is currently configured. The Solr
implementation uses the richer eDisMax query parser, so Elasticsearch `dis_max` is an approximation,
not a direct equivalent. Each normalized input is represented by a contiguous-token `phrase`
alternative and a looser `best_fields` alternative. Autocomplete adds a `phrase_prefix` alternative
for the incomplete final term.



```python

# elasticsearch query
def _build_elasticsearch_query(lookup_query: LookupQuery, filters: dict) -> dict:
    queries = []

    # Prefer a contiguous phrase, then allow a looser token match.
    for lookup_string in lookup_query.query_strings:
        queries.extend(
            [
                {
                    "multi_match": {
                        "query": lookup_string,
                        "type": "phrase",
                        "fields": ["preferred_name^30", "names^20"],
                    }
                },
                {
                    "multi_match": {
                        "query": lookup_string,
                        "type": "best_fields",
                        "fields": ["preferred_name^25", "names^10"],
                    }
                },
            ]
        )

    # https://www.elastic.co/search-labs/blog/elasticsearch-autocomplete-search#2.-query-time
    # Autocomplete treats the final query term as incomplete.
    if lookup_query.autocomplete:
        for lookup_string in lookup_query.query_strings:
            queries.append(
                {
                    "multi_match": {
                        "query": lookup_string,
                        "type": "phrase_prefix",
                        "fields": ["preferred_name^30", "names^20"],
                    }
                }
            )

    compound_lookup_query = {
        "bool": {
            "must": [
                {
                    "dis_max": {
                        "queries": queries,
                    }
                }
            ]
        }
    }
    # Keep constraints in filter context so they do not affect name-match scores.
    for key in ["filter", "must_not"]:
        if len(filters[key]) > 0:
            compound_lookup_query["bool"].setdefault(key, []).extend(filters[key])

    return compound_lookup_query

...

# solr query
if highlighting:
    inner_params.update(
        {
            "hl": "true",
            "hl.method": "unified",
            "hl.encoder": "html",
            "hl.tag.pre": "<strong>",
            "hl.tag.post": "</strong>",
        }
    )

params = {
    "query": {
        "edismax": {
            "query": query,
            # qf = query fields, i.e. how should we boost these fields if they contain the same fields as the input.
            # https://solr.apache.org/guide/solr/latest/query-guide/dismax-query-parser.html#qf-query-fields-parameter
            "qf": "preferred_name_exactish^250 names_exactish^100 preferred_name^25 names^10",
            # pf = phrase fields, i.e. how should we boost these fields if they contain the entire search phrase.
            # https://solr.apache.org/guide/solr/latest/query-guide/dismax-query-parser.html#pf-phrase-fields-parameter
            "pf": "preferred_name_exactish^300 names_exactish^200 preferred_name^30 names^20",
            # Boosts
            "bq": [],
            "boost": [
                # The boost is multiplied with score -- calculating the log() reduces how quickly this increases
                # the score for increasing clique identifier counts.
                "log(sum(clique_identifier_count, 1))"
            ],
        },
    },
    "sort": "score DESC, clique_identifier_count DESC, curie_suffix ASC",
    "limit": limit,
    "offset": offset,
    "filter": filters,
    "fields": "*, score",
    "params": inner_params,
}
```


##### Future Work and Optimizations

* Future Work
    * Elasticsearch currently uses `clique_identifier_count` as a secondary sort, but does not reproduce
        Solr's multiplicative `log(sum(clique_identifier_count, 1))` score boost.
    * Decide which taxon behavior is the compatibility target. Deployed Solr `v1.5.2` filters only on the
        requested taxa, while newer upstream Solr also admits records with `taxon_specific:false`; the
        Elasticsearch index does not currently expose that field.
    * The current Elasticsearch mapping does not expose normalized keyword subfields for names.
        The `phrase` query is therefore an exact contiguous-token preference, not true whole-field
        equality. Whole-field equality would require normalized keyword subfields and reindexing.
    * Likely some more rigorous testing akin to what we did with nodenorm. Will be harder due to the
        difference in scoring
    * Compare Elasticsearch's `phrase_prefix` autocomplete behavior with Solr's trailing-wildcard query
        and confirm the UI requirements.


```shell
# add lowercase text type
{
    "add-field-type" : {
        "name": "LowerTextField",
        "class": "solr.TextField",
        "positionIncrementGap": "100",
        "analyzer": {
            "tokenizer": {
                "class": "solr.StandardTokenizerFactory"
            },
            "filters": [{
                "class": "solr.LowerCaseFilterFactory"
            }]
        }
    }
}

# add exactish text type (as described at https://stackoverflow.com/a/29105025/27310)
{
    "add-field-type" : {
        "name": "exactish",
        "class": "solr.TextField",
        "positionIncrementGap": "100",
        "analyzer": {
            "tokenizer": {
                "class": "solr.KeywordTokenizerFactory"
            },
            "filters": [{
                "class": "solr.LowerCaseFilterFactory"
            }]
        }
    }
}



# solr schema
{
    "add-field": [
        {
            "name":"names",
            "type":"LowerTextField",
            "indexed":true,
            "stored":true,
            "multiValued":true
        },
        {
            "name":"names_exactish",
            "type":"exactish",
            "indexed":true,
            "stored":false,
            "multiValued":true
        },
        {
            "name":"curie",
            "type":"string",
            "stored":true
        },
        {
            "name":"preferred_name",
            "type":"LowerTextField",
            "stored":true
        },
        {
            "name":"preferred_name_exactish",
            "type":"exactish",
            "indexed":true,
            "stored":false,
            "multiValued":false
        },
        {
            "name":"types",
            "type":"string",
            "stored":true,
            "multiValued":true
        },
        {
            "name":"shortest_name_length",
            "type":"pint",
            "stored":true
        },
        {
            "name":"curie_suffix",
            "type":"plong",
            "docValues":true,
            "stored":true,
            "required":false,
            "sortMissingLast":true
        },
        {
            "name":"taxa",
            "type":"string",
            "stored":true,
            "multiValued":true
        },
        {
            "name":"taxon_specific",
            "type":"boolean",
            "stored":true,
            "multiValued":false,
            "sortMissingLast":true
        },
        {
            "name":"clique_identifier_count",
            "type":"pint",
            "stored":true
        }
    ]
}
```

* Optimizations
    * On the boosting note, if we need the boosting then we should handle it at index time rather than query
        time to avoid the penalty if performance is required. This will require additional work at
        index time, but if automated shouldn't be a problem
    * The bulk endpoint is just a for loop over the lookup method. Need to investigate if an `msearch`
        can handle parallelizing the bulk lookup endpoint, which is probably the one we need to optimize
        for performance
    * Other suggestions on improving the performance when looking stuff up from the index. The query is
        fairly complex, but there could be elasticsearch features we could leverage that improve the
        performance that I'm unaware of
