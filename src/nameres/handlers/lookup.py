"""
Lookup endpoints for the name-resolution service

Converted from SOLR -> Elasticsearch
"""

import dataclasses
import json
import re
from typing import Optional

from tornado.web import HTTPError

from nameres.handlers.base import NameResolutionBaseHandler
from nameres.namespace import NameResolutionAPINamespace


class LookupArgumentException(HTTPError):
    """A lookup request that does not match the advertised API contract."""

    def __init__(self, message: str = "Invalid lookup request"):
        super().__init__(status_code=422, reason=message)


@dataclasses.dataclass()
class LookupQuery:
    raw_string: str
    query_strings: tuple[str, ...]
    autocomplete: Optional[bool]
    highlighting: Optional[bool]
    offset: Optional[int]
    limit: Optional[int]


@dataclasses.dataclass()
class LookupResult:
    curie: str
    label: str
    highlighting: dict[str, list[str]]
    synonyms: list[str]
    taxa: list[str]
    types: list[str]
    score: float
    clique_identifier_count: int


class BaseNameResolutionLookupHandler(NameResolutionBaseHandler):
    """
    Base class for both the lookup and bulklookup endpoints

    We share an inheritence structure between the two, as they
    both have the same argument handling besides some small differences.
    So we create a `prepare` method that extracts those arguments,
    along with some additional auxillary methods for formatting
    things in the way we expect for elasticsearch
    """

    accepts_json_body = False
    json_body_options: frozenset[str] = frozenset()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lookup_queries: list[LookupQuery] = None
        self.filters: dict = None
        self.json_body_arguments: dict[str, object] = {}

    def write_error(self, status_code: int, **kwargs) -> None:
        """Return lookup validation failures in the documented JSON shape."""
        exception = kwargs.get("exc_info", (None, None, None))[1]
        if isinstance(exception, LookupArgumentException):
            self.finish_json(
                {
                    "detail": [
                        {
                            "loc": ["request"],
                            "msg": exception.reason,
                            "type": "value_error",
                        }
                    ]
                }
            )
            return

        super().write_error(status_code, **kwargs)

    def prepare(self) -> None:
        """Handles argument parsing any lookup requests.

        ``/lookup`` reads its search string and options from URL query arguments.
        ``/bulk-lookup`` reads the advertised JSON body, with URL query arguments
        retained as a backwards-compatible fallback for omitted body options.

        Argument Matrix:
        | argument_name    | type      | required | default |
        | string           | str       | True     | None    | < lookup GET|POST
        | strings          | list[str] | True     | None    | < bulklookup POST
        | autocomplete     | bool      | False    | False   |
        | highlighting     | bool      | False    | False   |
        | offset           | int       | False    | 0       |
        | limit            | int       | False    | 10      |
        | biolink_type(s)  | list[str] | False    | []      |
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
        Results with either would result in a match. For ``/lookup``, the singular
        ``biolink_type`` query parameter is repeatable and ``biolink_types`` is an
        accepted alias. The bulk JSON body uses the ``biolink_types`` array.

        only_prefixes: Pipe-separated, case-sensitive list of prefixes to filter.
        Examples: <"MONDO|EFO">, would apply filters for `MONDO` OR `EFO`

        exclude_prefixes: Pipe-separated, case-sensitive list of prefixes to exclude.
        Examples: <"UMLS|EFO"> would apply filters for `UMLS` or `EFO`

        only_taxa: Pipe-separated, case-sensitive list of taxa to filter.
        Examples: <"NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955">
        would apply taxa filters for each pipe separated entry
        """
        if self.request.method == "OPTIONS":
            return

        self.json_body_arguments = self._parse_json_body_arguments() if self.accepts_json_body else {}
        lookup_strings = self._parse_lookup_string_arguments()
        sanitized_lookup_strings = self._sanitize_lookup_query(lookup_strings)

        self.filters = self._build_lookup_filters()

        def parse_boolean(argument: str | bool) -> bool:
            if isinstance(argument, bool):
                return argument
            if isinstance(argument, str):
                return not argument.lower() == "false"
            return False

        autocomplete_option = parse_boolean(self._get_lookup_argument("autocomplete", default=False, strip=True))
        highlighting_option = parse_boolean(self._get_lookup_argument("highlighting", default=False, strip=True))
        try:
            offset_option = int(self._get_lookup_argument("offset", default=0, strip=True))
            limit_option = int(self._get_lookup_argument("limit", default=10, strip=True))
            if offset_option < 0 or limit_option < 0:
                raise ValueError
        except (TypeError, ValueError):
            lookup_message = (
                "Invalid literal for `offset` or `limit` option | " "offset and limit must be non-negative integers "
            )
            raise LookupArgumentException(lookup_message)

        self.lookup_queries = []
        for raw_string, query_strings in sanitized_lookup_strings:
            lookup_query = LookupQuery(
                raw_string=raw_string,
                query_strings=query_strings,
                autocomplete=autocomplete_option,
                highlighting=highlighting_option,
                offset=offset_option,
                limit=limit_option,
            )
            self.lookup_queries.append(lookup_query)

    def _parse_json_body_arguments(self) -> dict[str, object]:
        """Decode a request JSON object once so bulk options can share it."""
        if not self.request.body:
            return {}

        try:
            body_arguments = json.loads(self.request.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as json_exc:
            raise LookupArgumentException("Lookup request body must be valid JSON") from json_exc

        if not isinstance(body_arguments, dict):
            raise LookupArgumentException("Lookup request body must be a JSON object")

        scalar_types = {
            "autocomplete": (bool, "a boolean"),
            "highlighting": (bool, "a boolean"),
            "offset": (int, "an integer"),
            "limit": (int, "an integer"),
            "only_prefixes": (str, "a string"),
            "exclude_prefixes": (str, "a string"),
            "only_taxa": (str, "a string"),
        }
        for name, (expected_type, description) in scalar_types.items():
            argument = body_arguments.get(name)
            if name in body_arguments and argument is not None and type(argument) is not expected_type:
                raise LookupArgumentException(f"`{name}` must be {description}")

        strings = body_arguments.get("strings")
        if "strings" in body_arguments and (
            not isinstance(strings, list) or not all(isinstance(argument, str) for argument in strings)
        ):
            raise LookupArgumentException("`strings` must be an array of strings")

        biolink_types = body_arguments.get("biolink_types")
        if (
            "biolink_types" in body_arguments
            and biolink_types is not None
            and (
                not isinstance(biolink_types, list) or not all(isinstance(argument, str) for argument in biolink_types)
            )
        ):
            raise LookupArgumentException("`biolink_types` must be an array of strings")

        return body_arguments

    def _get_lookup_argument(self, name: str, *, default=None, strip: bool = True):
        """Read a bulk JSON option, falling back to the URL query argument."""
        if name in self.json_body_options and name in self.json_body_arguments:
            argument = self.json_body_arguments[name]
            if argument is None:
                return default
            if strip and isinstance(argument, str):
                return argument.strip()
            return argument

        return self.get_argument(name, default=default, strip=strip)

    def _get_lookup_arguments(self, name: str, *aliases: str) -> list[str]:
        """Read a list-valued bulk JSON option or its URL query aliases."""
        if name in self.json_body_options and name in self.json_body_arguments:
            arguments = self.json_body_arguments[name]
            if arguments is None:
                return []
            if not isinstance(arguments, list) or not all(isinstance(argument, str) for argument in arguments):
                raise LookupArgumentException(f"`{name}` must be an array of strings")
            return arguments

        return [argument for query_name in (name, *aliases) for argument in self.get_arguments(query_name)]

    def _get_pipe_delimited_lookup_argument(self, name: str) -> list[str]:
        """Parse one of the pipe-delimited filter options."""
        argument = self._get_lookup_argument(name, default="", strip=True)
        if not isinstance(argument, str):
            raise LookupArgumentException(f"`{name}` must be a string")
        return [value for value in argument.split("|") if value.strip()]

    def _parse_lookup_string_arguments(self) -> list[str]:
        """Attempt to determine if this is a singular or bulk lookup."""
        search_string = self.get_argument("string", default=None)
        missing = object()
        search_string_collection = self.json_body_arguments.get("strings", missing)

        if search_string_collection is not missing and (
            not isinstance(search_string_collection, list)
            or not all(isinstance(search_string, str) for search_string in search_string_collection)
        ):
            raise LookupArgumentException("`strings` must be an array of strings")

        if search_string is None and search_string_collection is missing:
            raise LookupArgumentException("Either `string` or `strings` must be supplied for lookup")

        if search_string is not None and search_string_collection is not missing:
            raise LookupArgumentException("Both `string` or `strings` cannot both be supplied for lookup")

        lookup_strings = []
        if search_string is not None and search_string_collection is missing:
            lookup_strings.append(search_string)
        elif search_string is None and search_string_collection is not missing:
            lookup_strings.extend(search_string_collection)
        return lookup_strings

    def _sanitize_lookup_query(self, lookup_strings: list[str]) -> list[tuple[str, tuple[str, ...]]]:
        r"""Performs input sanitization on the lookup query terms.

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
        """
        sanitized_lookup_strings = []
        for lookup_string in lookup_strings:
            raw_lookup_string = lookup_string.strip()
            lookup_string = raw_lookup_string.lower()

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

                query_strings = [lookup_string_with_escaped_groups]
                if fully_escaped_lookup_string != lookup_string_with_escaped_groups:
                    query_strings.append(fully_escaped_lookup_string)

                sanitized_lookup_strings.append((raw_lookup_string, tuple(query_strings)))

        return sanitized_lookup_strings

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
                es_filters["filter"].append({"bool": {"should": should_filters, "minimum_should_match": 1}})

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


class NameResolutionLookupHandler(BaseNameResolutionLookupHandler):
    """
    Mirror implementation to the renci implementation found at
    https://name-resolution-sri.renci.org/docs#/

    We intend to mirror the /lookup endpoint
    """

    name = "lookup"

    async def get(self):
        """Returns cliques with a name or synonym that contains a specified string."""
        try:
            lookup_result = await lookup(self.biothings, self.lookup_queries[0], self.filters)
        except Exception as gen_exc:
            raise HTTPError(detail="Error occurred during processing.", status_code=500) from gen_exc
        self.finish_json(lookup_result)

    async def post(self):
        """Returns cliques with a name or synonym that contains a specified string."""
        try:
            lookup_result = await lookup(self.biothings, self.lookup_queries[0], self.filters)
        except Exception as gen_exc:
            raise HTTPError(detail="Error occurred during processing.", status_code=500) from gen_exc
        self.finish_json(lookup_result)


class NameResolutionBulkLookupHandler(BaseNameResolutionLookupHandler):
    """
    Mirror implementation to the renci implementation found at
    https://name-resolution-sri.renci.org/docs#/

    We intend to mirror the /bulk-lookup endpoint
    """

    name = "bulk-lookup"
    accepts_json_body = True
    json_body_options = frozenset(
        {
            "autocomplete",
            "highlighting",
            "offset",
            "limit",
            "biolink_types",
            "only_prefixes",
            "exclude_prefixes",
            "only_taxa",
        }
    )

    async def post(self) -> None:
        """Returns cliques with a name or synonym that contains a specified string sent via batch."""

        try:
            lookup_results = {}
            for lookup_query in self.lookup_queries:
                lookup_result: list[dict] = await lookup(self.biothings, lookup_query, self.filters)
                lookup_results[lookup_query.raw_string] = lookup_result
        except Exception as gen_exc:
            raise HTTPError(detail="Error occurred during processing.", status_code=500) from gen_exc
        self.finish(lookup_results)


async def lookup(
    biothings_metadata: NameResolutionAPINamespace, lookup_query: LookupQuery, filters: dict
) -> list[dict]:
    """Returns cliques with a name or synonym that contains a specified string."""
    elasticsearch_query = _build_elasticsearch_query(lookup_query, filters)

    # Turn on highlighting if requested.
    highlight_configuration = None
    if lookup_query.highlighting:
        highlight_configuration = {
            "type": "unified",
            "encoder": "html",
            "require_field_match": False,
            "fields": {
                "names": {"pre_tags": ["<strong>"], "post_tags": ["</strong>"]},
                "preferred_name": {"pre_tags": ["<strong>"], "post_tags": ["</strong>"]},
            },
        }

    search_result_ordering = [{"_score": "desc"}, {"clique_identifier_count": "desc"}]

    search_parameters = {
        "query": elasticsearch_query,
        "index": biothings_metadata.elasticsearch.indices,
        "highlight": highlight_configuration,
        "size": lookup_query.limit,
        "sort": search_result_ordering,
        "from": lookup_query.offset,
    }
    lookup_response = await biothings_metadata.elasticsearch.async_client.search(**search_parameters)

    outputs = []
    for doc in lookup_response["hits"]["hits"]:
        preferred_matches = []
        synonym_matches = []

        highlighting_response = doc.get("highlight", None)
        if highlighting_response is not None and isinstance(highlighting_response, dict):
            synonym_matches.extend(highlighting_response.get("names", []))
            preferred_matches.extend(highlighting_response.get("preferred_name", []))

        source = doc["_source"]
        curie_identifier = source.get("curie", "")
        outputs.append(
            dataclasses.asdict(
                LookupResult(
                    curie=curie_identifier,
                    label=source.get("preferred_name", ""),
                    highlighting=(
                        {
                            "labels": preferred_matches,
                            "synonyms": synonym_matches,
                        }
                        if lookup_query.highlighting
                        else {}
                    ),
                    synonyms=source.get("names", []),
                    score=doc.get("_score", ""),
                    taxa=source.get("taxa", []),
                    clique_identifier_count=source.get("clique_identifier_count", 0),
                    types=[f"biolink:{d}" for d in source.get("biolink_types", [])],
                )
            )
        )

    return outputs


def _build_elasticsearch_query(lookup_query: LookupQuery, filters: dict) -> dict:
    queries = []

    # Base Query
    for lookup_string in lookup_query.query_strings:
        queries.append(
            {
                "multi_match": {
                    "query": lookup_string,
                    "type": "best_fields",
                    "fields": ["preferred_name^25", "names^10"],
                }
            }
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
