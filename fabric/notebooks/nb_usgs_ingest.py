# Fabric notebook: nb_usgs_ingest  (scheduled every 5 minutes)
#
# Keyless, incremental ingestion of the USGS global earthquake feed into a Fabric Eventhouse.
# Runs under the notebook owner's Fabric identity: no connection strings, keys or secrets.
#
#   1) Fn_UsgsNewEvents()  - the Eventhouse reads the USGS past-day GeoJSON itself (externaldata)
#                            and returns only events that are new or revised (anti-join on EventId+Updated)
#   2) Fn_NewSilverRows()  - types, enriches and de-duplicates those rows for the silver table
#
# Set these two IDs for your own workspace (Fabric URL: /groups/<WORKSPACE_ID>/eventhouses/<EVENTHOUSE_ID>)
import requests, notebookutils

WORKSPACE_ID  = "<your-workspace-id>"
EVENTHOUSE_ID = "<your-eventhouse-id>"
DATABASE      = "QuakeHouse"

fabric_token = notebookutils.credentials.getToken("pbi")
meta = requests.get(
    f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/eventhouses/{EVENTHOUSE_ID}",
    headers={"Authorization": "Bearer " + fabric_token},
).json()
query_uri = meta["properties"]["queryServiceUri"]

headers = {"Authorization": "Bearer " + notebookutils.credentials.getToken("kusto"),
           "Content-Type": "application/json"}

for cmd in [".set-or-append QuakesRaw <| Fn_UsgsNewEvents()",     # bronze: new/revised raw events
            ".set-or-append Quakes <| Fn_NewSilverRows()"]:        # silver: cleaned, enriched rows
    r = requests.post(query_uri + "/v1/rest/mgmt", headers=headers, json={"db": DATABASE, "csl": cmd})
    print(cmd, "->", r.status_code, "ok" if r.ok else r.text[:300])
    r.raise_for_status()
