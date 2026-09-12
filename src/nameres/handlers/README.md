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
fastapi version. So the argument structure is the same for both

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

biolink_types: The Biolink types to filter to (with or without the `biolink:` prefix).
Examples: <["biolink:Disease", "biolink:PhenotypicFeature"]>, would apply
filtering for the types `biolink:Disease` OR `biolink:PhenotypicFeature`.
Results with either would result in a match

only_prefixes: Pipe-separated, case-sensitive list of prefixes to filter.
Examples: <"MONDO|EFO">, would apply filters for `MONDO` OR `EFO`

exclude_prefixes: Pipe-separated, case-sensitive list of prefixes to exclude.
Examples: <"UMLS|EFO"> would apply filters for `UMLS` or `EFO`

only_taxa: Pipe-separated, case-sensitive list of taxa to filter.
Examples: <"NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955">
would apply taxa filters for each pipe separated entry
```


##### search phrase sanitization
This operation was already set in place by the solr endpoint, it's effectively just attempting
to ensure that we have a proper encoding and that any special characters are escaped.

Sanitization Operations:
1) strip and lowercase the query (all indexes are case-insensitive)
2) evaluate string encoding
    There is a possibility that the input text isn't in UTF-8.
    Python packages that try to determine what the encoding is:
    - https://pypi.org/project/charset-normalizer/
    - https://www.crummy.com/software/BeautifulSoup/bs4/doc/#unicode-dammit
    But the only issue we've run into so far has been the Windows smart
    quote (https://github.com/TranslatorSRI/NameResolution/issues/176), so
    let's detect and replace just those characters.
3) prune any empty string searches
    If there's nothing to search don't perform any search
4) escape special characters
    We need to use backslash to escape characters
       ( e.g. "\(" )
    to remove the special significance of characters
    inside round brackets, but not inside double-quotes.
    So we escape them separately:
    - For a full exact search, we only remove double-quotes
    and slashes, leaving other special characters as-is.
5) escape special characters for tokenization
    we escape all special characters with backslashes as well as
    other characters that might mess up the search.


```python
def _sanitize_lookup_query(self, lookup_strings: list[str]) -> list[tuple[str]]:
    sanitized_lookup_strings = []
    for lookup_string in lookup_strings:
        lookup_string = lookup_string.strip().lower()

        windows_smart_single_quote_pattern = r"[‘’]"
        windows_smart_double_quote_pattern = r"[“”]"

        lookup_string = re.sub(windows_smart_single_quote_pattern, "'", lookup_string)
        lookup_string = re.sub(windows_smart_double_quote_pattern, '"', lookup_string)

        if lookup_string is not None and lookup_string != "":
            lookup_string_with_escaped_groups = lookup_string.replace("\\", "")
            lookup_string_with_escaped_groups = lookup_string_with_escaped_groups.replace('"', "")

            # Regex overview
            # r'[!(){}\[\]^"~*?:/+-\\]'
            # Match a single character present in the list below [!(){}\[\]^"~*?:/+-\\]
            # !(){}
            #  matches a single character in the list !(){} (case sensitive)
            # \[ matches the character [ with index 9110 (5B16 or 1338) literally (case sensitive)
            # \] matches the character ] with index 9310 (5D16 or 1358) literally (case sensitive)
            # ^"~*?:/
            #  matches a single character in the list ^"~*?:/ (case sensitive)
            # +-\\ matches a single character in the range between + (index 43) and \ (index 92) (case sensitive)
            special_characters_group = r'[!(){}\[\]^"~*?:/+-\\]'

            # \g<0> is a backreference which will insert the text most recently matched by
            # entire pattern. So in this case, because the entire pattern is the special
            # characters group we wish to escape, it will surrond the last matched special
            # character with quotes and backslash
            # Example: query_term? -> query_term"\?"
            substitution_escape_backreference = r"\\\g<0>"
            fully_escaped_lookup_string = re.sub(
                special_characters_group, substitution_escape_backreference, lookup_string
            )

            fully_escaped_lookup_string = fully_escaped_lookup_string.replace("&&", " ")
            fully_escaped_lookup_string = fully_escaped_lookup_string.replace("||", " ")

            sanitized_lookup_strings.append(set([lookup_string_with_escaped_groups, fully_escaped_lookup_string]))

    return sanitized_lookup_strings
```

##### filters

Four optional filter categories can constrain a lookup:

- `biolink_type` and `biolink_types` produce exact `term` queries on `biolink_types`.
- `only_prefixes` produces `prefix` queries on `curie`.
- `only_taxa` produces exact `term` queries on `taxa`.
- `exclude_prefixes` produces `prefix` queries under `must_not`.

Values within each positive category are combined with OR. The positive categories themselves are
separate entries under `bool.filter`, so supplying more than one category combines them with AND.
Excluded prefixes are placed under `must_not`. These clauses run in filter context and therefore do
not change the text relevance score. For example:

```JSON
{
    "filter": [
        {
            "bool": {
                "should": [
                    {"term": {"biolink_types": "Disease"}},
                    {"term": {"biolink_types": "PhenotypicFeature"}}
                ],
                "minimum_should_match": 1
            }
        },
        {
            "bool": {
                "should": [
                    {"prefix": {"curie": "MONDO"}},
                    {"prefix": {"curie": "HP"}}
                ],
                "minimum_should_match": 1
            }
        },
        {
            "bool": {
                "should": [
                    {"term": {"taxa": "NCBITaxon:9606"}}
                ],
                "minimum_should_match": 1
            }
        }
    ],
    "must_not": [
        {"prefix": {"curie": "UMLS"}}
    ]
}
```

Empty categories are omitted. See `_build_lookup_filters` in `lookup.py` for the request parsing and
query construction.


##### build Elasticsearch query

The text-matching `dis_max` is the only scoring clause in the inner `bool` query. Positive filter
categories and excluded prefixes are attached to `filter` and `must_not`, respectively, so they
constrain matches without contributing to the text score. A top-level `function_score` then applies
the same logarithmic clique-size multiplier used by the Solr implementation.

```JSON
{
    "function_score": {
        "query": {
            "bool": {
                "must": [
                    {
                        "dis_max": {
                            "queries": [
                                {
                                    "multi_match": {
                                        "query": "<lookup string>",
                                        "type": "best_fields",
                                        "fields": ["preferred_name^25", "names^10"]
                                    }
                                },
                                {
                                    "multi_match": {
                                        "query": "<lookup string>",
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
                            "should": [
                                {"term": {"biolink_types": "Disease"}}
                            ],
                            "minimum_should_match": 1
                        }
                    }
                ],
                "must_not": [
                    {"prefix": {"curie": "UMLS"}}
                ]
            }
        },
        "field_value_factor": {
            "field": "clique_identifier_count",
            "modifier": "log1p",
            "missing": 0
        },
        "boost_mode": "multiply"
    }
}
```

One `best_fields` query is generated for every sanitized lookup string. The `phrase_prefix` queries
are included only for autocomplete requests. `log1p` is the common logarithm after adding one, so
the final score is:

`text score * log10(clique_identifier_count + 1)`

See `_build_elasticsearch_query` in `lookup.py` for the authoritative implementation.

For comparison, the Solr implementation applies the same multiplier:

```python

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
    * Add the `taxon_specific` field to the index. I missed this when looking through the solr schema.
        Only used in taxon filtering at the moment
    * We have a difference in the index as they created custom field types that duplicate the
    some of the content of the field in the index that likely increases the size by a moderate amount.
    At the moment I haven't done this to see if we even need to perform the additional indexing. The
    additional fields and solr indexing is shown below
    * Likely some more rigorous testing akin to what we did with nodenorm. Will be harder due to the
        difference in scoring
    * Discuss with UI team what they require from an autocomplete perspective. The `autocomplete` option
        more just searches for phrases rather than terms, and more advanced runtime autocomplete options
        exists within elasticsearch


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
            "stored":true
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
