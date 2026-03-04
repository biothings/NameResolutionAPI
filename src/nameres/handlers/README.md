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

We have 4 different filters we have to apply to our query depending on what the user
supplies

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

This is different from solr, but only syntatically. We have to include these filters within
the main query in elasticsearch, whereas solr provides a filter in the query that includes 
boolean logic combining the field:value pairs in a similar fashion to our `should` and `must_not`
clauses above


```python
def _build_lookup_filters(self) -> dict:
    """Handles the parsing and building of various elasticsearch boolean logic queries.

    We have two types of boolean logic queries we need to build for this endpoint

    1) should
    In this case we want to boolean OR specific different types of required
    fields we want in the results output

    2) must_not
    In this case we to boolean AND NOT specific different types of required
    fields we want to ensure `don't` exist in the results output
    """
    biolink_types = self.get_argument("biolink_types", default=[], strip=True)

    filter_delimiter = "|"

    only_prefixes = self.get_argument("only_prefixes", default="", strip=True)
    only_prefixes = only_prefixes.split(filter_delimiter)
    try:
        only_prefixes.remove("")
    except ValueError:
        pass

    exclude_prefixes = self.get_argument("exclude_prefixes", default="", strip=True)
    exclude_prefixes = exclude_prefixes.split(filter_delimiter)
    try:
        exclude_prefixes.remove("")
    except ValueError:
        pass

    only_taxa = self.get_argument("only_taxa", default="", strip=True)
    only_taxa = only_taxa.split(filter_delimiter)
    try:
        only_taxa.remove("")
    except ValueError:
        pass

    # Apply filters as needed.
    filters = {"should": [], "must_not": []}

    # Biolink type filter
    # Elasticsearch should
    for biolink_type in biolink_types:
        biolink_type = biolink_type.strip()
        if biolink_type is not None:
            should_filter = {"term": {"biolink_types": biolink_type.remove("biolink:")}}
            filters["should"].append(should_filter)

    # Prefix: only filter
    # Elasticsearch should + Match boolean prefix query
    for prefix in only_prefixes:
        prefix = prefix.strip()
        should_filter = {"prefix": {"curie": prefix}}
        filters["should"].append(should_filter)

    # Prefix: exclude filter
    # Elasticsearch must not
    for prefix in exclude_prefixes:
        prefix = prefix.strip()
        must_not_filter = {"prefix": {"curie": prefix}}
        filters["must_not"].append(must_not_filter)

    # Taxa filter.
    # only_taxa is like: 'NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955'
    # Elasticsearch should
    for taxon in only_taxa:
        taxon = taxon.strip()
        should_filter = {"term": {"taxa": taxon}}
        filters["should"].append(should_filter)

    # We also need to include entries that don't have taxa specified.
    # TODO Skipping for the moment as we need to update the index
    # filters["should"].append({ "term" : { "taxon_specific" : False } }

    return filters
```


##### build elasticsearch query

So this query is fairly complicated because we have a lot of specifications we want to achieve from
our lookup. The overall structure of the query is the following:

```JSON
{
    "bool": {
        "must": [
            {
                "dis_max": {
                    "queries": [
                        {
                            "multi_match": {
                                "query": lookup_string0,
                                "type": "best_fields",
                                "fields": ["preferred_name^25", "name^10"],
                            }
                        },
                        {
                            "multi_match": {
                                "query": lookup_string1,
                                "type": "best_fields",
                                "fields": ["preferred_name^25", "name^10"],
                            }
                        },

                        # autocomplete queries

                        {
                            "multi_match": {
                                "query": lookup_string0,
                                "type": "phrase",
                                "fields": ["preferred_name^30", "name^20"],
                            }
                        },
                        {
                            "multi_match": {
                                "query": lookup_string1,
                                "type": "phrase",
                                "fields": ["preferred_name^30", "name^20"],
                            }
                        }
                    ]
                }
            },
            {
                "should":[<insert should filters>]
            }
        ]
    },
    "must_not": [<insert must_not filters>]
}
```

The `dis_max` (disjunction maximization) filter in this case will return documents that match one of
more of the provided queries. If multiple match than it selects amongest the highest relevance
scoring with tie breaking capabilities based off additional submatching. The original solr index
leveraged a more advanced version called the extended disjunction max query that is specific to
solr. Elasticsearch doesn't currently implement this version so we leverage the standard `dis_max`.
From the string search santization we break each query into a separate `multi_match`. This is also
how we incorporate the autocomplete version, as we also extend additional queries to leverage
`phrase` based matches compared to the standard of `best_fields`



```python

# elasticsearch query
def _build_elasticsearch_query(lookup_query: list[LookupQuery], filters: dict) -> dict:
    queries = []

    # Base Query
    for lookup_string in lookup_query.string:
        queries.append(
            {
                "multi_match": {
                    "query": lookup_string,
                    "type": "best_fields",
                    "fields": ["preferred_name^25", "name^10"],
                }
            }
        )

    # https://www.elastic.co/search-labs/blog/elasticsearch-autocomplete-search#2.-query-time
    if lookup_query.autocomplete:
        for lookup_string in lookup_query.string:
            queries.append(
                {
                    "multi_match": {
                        "query": lookup_string,
                        "type": "phrase",
                        "fields": ["preferred_name^30", "name^20"],
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
    if len(filters["should"]) > 0:
        compound_lookup_query["bool"]["must"].append({"bool": {"should": [*filters["should"]]}})

    if len(filters["must_not"]) > 0:
        compound_lookup_query["bool"]["must_not"] = [*filters["must_not"]]

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
    * Need to figure out how incorporate boosting leveraging the `clique_identifier_count` 
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
