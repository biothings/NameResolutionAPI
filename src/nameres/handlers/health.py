from urllib.parse import urlparse

import elastic_transport
from elasticsearch import AsyncElasticsearch

from biothings.web.handlers import BaseHandler

from nameres.biolink import BIOLINK_MODEL_VERSION


class NameResolutionHealthHandler(BaseHandler):
    """
    Important Endpoints
    * /_cat/nodes
    * /{index}/stats
    """

    name = "health"

    async def get(self):
        async_client: AsyncElasticsearch = self.biothings.elasticsearch.async_client
        search_indices = self.biothings.elasticsearch.indices

        babel_version = None

        try:
            nameres_index_metadata: elastic_transport.ObjectApiResponse = await async_client.indices.get(
                index=search_indices
            )

            # greedy approach to extract the first index from the body
            for search_index in search_indices:
                index_body = nameres_index_metadata.body.get(search_index, None)
                if index_body is not None:
                    compendia_url = index_body["mappings"]["_meta"]["src"]["nameres"]["url"]
                    parsed_compendia_url = urlparse(compendia_url)
                    babel_version = parsed_compendia_url.path.split("/")[-2]
                    break

            index_stats_response = await self.biothings.elasticsearch.async_client.indices.stats(
                index=search_indices, metric=["docs", "segments"]
            )

            index_statistics = {"numDocs": 0, "deletedDocs": 0, "segmentCount": 0, "size": 0}

            # greedy approach to extract the first index from the body
            for search_index in search_indices:
                stats_body = index_stats_response["indices"].get(search_index, None)
                if stats_body is not None:
                    index_statistics = {
                        "numDocs": stats_body["total"]["docs"].get("count", ""),
                        "deletedDocs": stats_body["total"]["docs"].get("deleted", ""),
                        "segmentCount": stats_body["total"]["segments"].get("count", ""),
                        "size": f'{stats_body["total"]["docs"].get("total_size_in_bytes", "") / 10**9} GB',
                    }
                    break

        except Exception:
            status_response = {
                "status": "error",
                "babel_version": babel_version,
            }
        else:
            status_response = {
                "status": "ok",
                "message": "Reporting results from primary index.",
                "babel_version": babel_version,
                "biolink_model_toolkit_version": BIOLINK_MODEL_VERSION,
                **index_statistics,
            }

        self.finish(status_response)
