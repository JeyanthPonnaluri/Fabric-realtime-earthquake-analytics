# Dashboard-as-code for the "Quake Live" Fabric Real-Time Dashboard.
#
# Pulls the dashboard definition through the Fabric REST API, rebuilds every tile
# (KPI strip, map, coloured tables, stacked trend, depth cross-section, regions,
# pipeline heartbeat), and pushes it back with updateDefinition.
# Run inside a Fabric notebook (uses notebookutils for the signed-in user's token).
import requests, notebookutils, base64, json, time

WORKSPACE_ID = "<your-workspace-id>"
DASHBOARD_ID = "<your-kql-dashboard-id>"
API = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/kqlDashboards/{DASHBOARD_ID}"
H = {"Authorization": "Bearer " + notebookutils.credentials.getToken("pbi")}

# ---- 1. read the current definition (long-running operation) ----
r = requests.post(API + "/getDefinition", headers=H)
if r.status_code == 202:
    loc = r.headers["Location"]
    while requests.get(loc, headers=H).json().get("status") not in ("Succeeded", "Failed"):
        time.sleep(3)
    r = requests.get(loc + "/result", headers=H)
parts = {p["path"]: json.loads(base64.b64decode(p["payload"]))
         for p in r.json()["definition"]["parts"] if p["path"].endswith(".json")}
D = parts["RealTimeDashboard.json"]   # needs at least one existing column, table and map tile to copy styling from

# ---- 2. rebuild tiles (B keeps a backup: push(B) restores the original) ----
import copy, uuid
B=copy.deepcopy(D); DS=D["dataSources"][0]["id"]; PG=D["pages"][0]["id"]; CO=copy.deepcopy(next(t for t in D["tiles"] if t["visualType"]=="column")["visualOptions"]); TB=copy.deepcopy(next(t for t in D["tiles"] if t["visualType"]=="table")["visualOptions"]); MP=copy.deepcopy(next(t for t in D["tiles"] if t["visualType"]=="map")["visualOptions"])
Q=[]; T=[]; EQ='QuakesLatest | where EventType == "earthquake"'
def add(title,vt,q,x,y,w,h,opt): qid=str(uuid.uuid4()); Q.append({"dataSource":{"kind":"inline","dataSourceId":DS},"text":q,"id":qid,"usedVariables":[]}); T.append({"id":str(uuid.uuid4()),"title":title,"visualType":vt,"pageId":PG,"layout":{"x":x,"y":y,"width":w,"height":h},"queryRef":{"kind":"query","queryId":qid},"visualOptions":opt})
def rule(name,op,v,color): return {"id":str(uuid.uuid4()),"ruleType":"colorByCondition","applyToColumn":"Magnitude","hideText":False,"applyTo":"cells","conditions":[{"operator":op,"column":"Magnitude","values":[v]}],"chainingOperator":"and","colorStyle":"light","color":color,"tag":"","icon":None,"ruleName":name,"visualType":"table","colorPalette":"blue","numberOfColors":None}
KPI='let d = '+EQ+' | where EventTime > ago(24h); let w = '+EQ+' | where EventTime > ago(7d); union (d | summarize Value = tostring(count()) | extend Label = "Earthquakes, last 24 h", o = 1), (d | summarize Value = tostring(countif(Magnitude >= 4.5)) | extend Label = "M4.5 and above, 24 h", o = 2), (w | summarize Value = strcat("M", tostring(round(max(Magnitude), 1))) | extend Label = "Strongest, 7 days", o = 3), (w | summarize Value = strcat(tostring(round(sum(pow(10.0, 1.5 * Magnitude + 4.8)) / 4.184e12, 0)), " kt") | extend Label = "Energy released, 7 days (TNT)", o = 4), (w | summarize Value = tostring(countif(DepthKm >= 300)) | extend Label = "Deep events (300 km+), 7 days", o = 5), (Quakes | summarize Value = strcat(tostring(datetime_diff("minute", now(), max(IngestedAt))), " min") | extend Label = "Since last pipeline run", o = 6) | order by o asc | project Label, Value'
add("Live status","multistat",KPI,0,0,24,3,{"multiStat__textSize":"auto","multiStat__valueColumn":"Value","multiStat__labelColumn":"Label","multiStat__displayOrientation":"horizontal","multiStat__slot":{"width":6,"height":1},"colorRulesDisabled":True,"colorStyle":"light","colorRules":[],"crossFilter":[],"crossFilterDisabled":False,"drillthroughDisabled":False,"drillthrough":[]})
add("Where the ground moved · 7 days, M4+ (colour = magnitude band)","map",EQ+' | where EventTime > ago(7d) and Magnitude >= 4 | project Longitude, Latitude, Magnitude, MagBand',0,3,15,11,{**MP,"map__latitudeColumn":"Latitude","map__longitudeColumn":"Longitude","map__labelColumn":"MagBand","map__sizeColumn":"Magnitude","map__sizeDisabled":False})
add("Latest M4+ earthquakes","table",EQ+' | where Magnitude >= 4 | top 15 by EventTime desc | project ["Time (IST)"] = format_datetime(datetime_add("minute", 330, EventTime), "dd-MM HH:mm"), Magnitude = round(Magnitude, 1), Place, ["Depth km"] = round(DepthKm, 0)',15,3,9,11,{**TB,"colorRulesDisabled":False,"colorRules":[rule("M5+",">=","5","red"),rule("M4-5","<","5","yellow")]})
add("Earthquakes per 6 hours by magnitude band","stackedcolumn",'QuakesHourly | where Hour > ago(7d) and EventType == "earthquake" and MagBand != "Micro (<2.5)" | summarize Events = sum(Events) by Hour = bin(Hour, 6h), MagBand | order by Hour asc, MagBand asc',0,14,12,8,{**CO,"xColumn":"Hour","yColumns":["Events"],"seriesColumns":["MagBand"],"seriesColors":{"Minor (2.5-4)":"rgba(242,180,136,1)","Light (4-5)":"rgba(224,123,60,1)","Moderate (5-6)":"rgba(180,71,27,1)","Strong (6-7)":"rgba(110,36,16,1)","Major (7+)":"rgba(58,15,5,1)"},"legendLocation":"bottom"})
add("Depth cross-section · 7 days (km below surface)","scatter",EQ+' | where EventTime > ago(7d) and Magnitude >= 2.5 | project Magnitude, ["Depth (km)"] = -DepthKm, DepthClass',12,14,12,8,{**CO,"xColumn":"Magnitude","yColumns":["Depth (km)"],"seriesColumns":["DepthClass"],"seriesColors":{"Shallow (<70 km)":"rgba(63,181,168,1)","Intermediate (70-300 km)":"rgba(33,124,128,1)","Deep (300+ km)":"rgba(18,69,90,1)"},"legendLocation":"bottom","xColumnTitle":"Magnitude"})
add("Most active regions · 7 days, M4+","bar",EQ+' | where EventTime > ago(7d) and Magnitude >= 4 | summarize Events = count() by Region | top 10 by Events',0,22,8,8,{**CO,"xColumn":"Region","yColumns":["Events"],"seriesColors":{"Events":"rgba(224,123,60,1)"},"hideLegend":True})
add("Pipeline heartbeat · new events per 15 min, last 6 h","column",'Quakes | where IngestedAt > ago(6h) | summarize ["New events"] = count() by Ingested = bin(IngestedAt, 15m) | order by Ingested asc',8,22,8,8,{**CO,"xColumn":"Ingested","yColumns":["New events"],"seriesColors":{"New events":"rgba(33,124,128,1)"},"hideLegend":True})
add("Strongest this week","table",EQ+' | where EventTime > ago(7d) | top 8 by Magnitude desc | project Magnitude = round(Magnitude, 1), Place, ["Energy (kt TNT)"] = round(pow(10.0, 1.5 * Magnitude + 4.8) / 4.184e12, 1)',16,22,8,8,{**TB,"colorRulesDisabled":False,"colorRules":[rule("M6+",">=","6","red"),rule("M5-6","<","6","yellow")]})
D2=copy.deepcopy(D); D2["tiles"]=T; D2["queries"]=Q
def push(DD): pl=base64.b64encode(json.dumps(DD).encode()).decode(); u=requests.post(API+"/updateDefinition",headers=H,json={"definition":{"parts":[{"path":"RealTimeDashboard.json","payload":pl,"payloadType":"InlineBase64"}]}}); print(u.status_code, u.text[:600]); return u
u=push(D2)
