"""Self-contained HTML report: no external assets, inline SVG charts, small vanilla JS for hover."""

from __future__ import annotations

import html
import json
from datetime import date, datetime

from .analysis import AI_CATS

SERIES = ["training", "search", "user", "classic"]
LABEL = {"training": "Training", "search": "AI search", "user": "User fetch", "classic": "Classic search"}

CSS = """
.viz-root{color-scheme:light;--surface-0:#f4f3f0;--surface-1:#fcfcfb;--text-primary:#0b0b0b;--text-secondary:#52514e;
 --text-muted:#7a7974;--grid:#e6e5e1;--axis:#c9c8c3;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;
 --neutral:#b9b8b2;--critical:#d03b3b;--good:#0ca30c;--warning:#fab219}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;
 --surface-0:#111110;--surface-1:#1a1a19;--text-primary:#f0efec;--text-secondary:#c3c2b7;--text-muted:#8f8e86;
 --grid:#2c2c2a;--axis:#4a4a46;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--neutral:#5d5c57}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface-0:#111110;--surface-1:#1a1a19;--text-primary:#f0efec;
 --text-secondary:#c3c2b7;--text-muted:#8f8e86;--grid:#2c2c2a;--axis:#4a4a46;--s1:#3987e5;--s2:#d95926;--s3:#199e70;
 --s4:#c98500;--neutral:#5d5c57}
*{box-sizing:border-box}
body{margin:0}
.viz-root{background:var(--surface-0);color:var(--text-primary);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;
 min-height:100vh;padding:32px 24px 64px}
main{max-width:1120px;margin:0 auto}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:0 0 4px}h3{font-size:14px;margin:16px 0 6px}
.sub{color:var(--text-secondary);margin:0 0 20px}
.card{background:var(--surface-1);border:1px solid var(--grid);border-radius:10px;padding:18px 20px;margin:0 0 16px}
.card p.note{color:var(--text-secondary);margin:2px 0 12px;font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:0 0 16px}
.tile{background:var(--surface-1);border:1px solid var(--grid);border-radius:10px;padding:14px 16px}
.tile .label{color:var(--text-secondary);font-size:13px}.tile .value{font-size:26px;font-weight:600;margin-top:2px}
.tile .hint{color:var(--text-muted);font-size:12px}
.legend{display:flex;flex-wrap:wrap;gap:14px;margin:6px 0 4px;color:var(--text-secondary);font-size:13px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.key-line{width:16px;height:2px;border-radius:1px;display:inline-block}
.key-rect{width:10px;height:10px;border-radius:2px;display:inline-block}
svg text{fill:var(--text-secondary);font-size:11px}
svg .grid{stroke:var(--grid);stroke-width:1}svg .axis{stroke:var(--axis);stroke-width:1}
.chart{position:relative}
.tip{position:absolute;pointer-events:none;background:var(--surface-1);border:1px solid var(--axis);border-radius:8px;
 padding:8px 10px;font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.12);display:none;min-width:150px;z-index:2}
.tip .t{color:var(--text-secondary);margin-bottom:4px}.tip .row{display:flex;align-items:center;gap:8px;justify-content:space-between}
.tip .row b{font-weight:600;color:var(--text-primary)}.tip .row span{color:var(--text-secondary);display:inline-flex;align-items:center;gap:6px}
table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;color:var(--text-secondary);font-weight:500;border-bottom:1px solid var(--axis);padding:6px 8px}
td{border-bottom:1px solid var(--grid);padding:6px 8px;vertical-align:top}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
td code,li code{font-size:12px}
details summary{cursor:pointer;color:var(--text-secondary);font-size:13px;margin-top:8px}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px}
.badge{display:inline-block;font-size:11px;padding:1px 7px;border-radius:9px;border:1px solid var(--axis);color:var(--text-secondary)}
ul.lint{list-style:none;padding:0;margin:0}ul.lint li{padding:6px 0;border-bottom:1px solid var(--grid)}
.sev{display:inline-block;width:68px;font-size:12px;color:var(--text-secondary)}
.sev::before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
.sev.error::before{background:var(--critical)}.sev.warning::before{background:var(--warning)}.sev.info::before{background:var(--neutral)}
.warn{border-left:3px solid var(--warning);padding:8px 12px;background:var(--surface-1);margin:0 0 16px;border-radius:4px}
.toggle{float:right;font-size:12px;background:none;border:1px solid var(--axis);color:var(--text-secondary);border-radius:6px;padding:3px 8px;cursor:pointer}
"""

JS = r"""
const D = JSON.parse(document.getElementById('data').textContent);
const fmt = n => n.toLocaleString('en-US');
function el(tag, attrs, parent){const e=document.createElementNS('http://www.w3.org/2000/svg',tag);
  for(const k in attrs) e.setAttribute(k, attrs[k]); if(parent) parent.appendChild(e); return e;}
function tipRow(tip, color, label, value, rect){const r=document.createElement('div'); r.className='row';
  const s=document.createElement('span'); const k=document.createElement('i'); k.className=rect?'key-rect':'key-line';
  k.style.background=color; s.appendChild(k); s.appendChild(document.createTextNode(label));
  const b=document.createElement('b'); b.textContent=value; r.appendChild(s); r.appendChild(b); tip.appendChild(r);}
function niceMax(v){if(v<=0)return 1;const p=Math.pow(10,Math.floor(Math.log10(v)));for(const m of[1,2,2.5,5,10]){if(m*p>=v)return m*p;}return 10*p;}
function compact(v){return v>=10000? (v/1000).toFixed(0)+'K' : fmt(v);}

// ---------------------------------------------------------------- line chart
(function(){
  const host=document.getElementById('daily'); if(!host) return;
  const days=D.days, series=D.series.filter(s=>s.values.some(v=>v>0));
  const W=host.clientWidth||900, H=300, m={l:48,r:96,t:12,b:28}, iw=W-m.l-m.r, ih=H-m.t-m.b;
  const svg=el('svg',{width:W,height:H,viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':'Daily AI crawler requests by type'},host);
  const max=niceMax(Math.max(...series.flatMap(s=>s.values)));
  const x=i=>m.l+(days.length<2?iw/2:i*iw/(days.length-1)), y=v=>m.t+ih-v/max*ih;
  for(let k=0;k<=4;k++){const v=max*k/4; el('line',{x1:m.l,x2:m.l+iw,y1:y(v),y2:y(v),class:k?'grid':'axis'},svg);
    const t=el('text',{x:m.l-8,y:y(v)+4,'text-anchor':'end'},svg); t.textContent=compact(v);}
  const step=Math.ceil(days.length/8);
  days.forEach((d,i)=>{const last=i===days.length-1; if((i%step===0&&days.length-1-i>=step/2)||last){const t=el('text',{x:x(i),y:H-8,'text-anchor':'middle'},svg); t.textContent=d.slice(5);}});
  const ends=[];
  series.forEach(s=>{
    el('path',{d:s.values.map((v,i)=>(i?'L':'M')+x(i).toFixed(1)+','+y(v).toFixed(1)).join(''),fill:'none',
      stroke:`var(${s.color})`,'stroke-width':2,'stroke-linejoin':'round','stroke-linecap':'round'},svg);
    const last=s.values.length-1;
    el('circle',{cx:x(last),cy:y(s.values[last]),r:4,fill:`var(${s.color})`,stroke:'var(--surface-1)','stroke-width':2},svg);
    ends.push({y:y(s.values[last]),label:s.label});
  });
  // direct end labels; skip any that would collide (the legend carries identity)
  ends.sort((a,b)=>a.y-b.y); let lastY=-99;
  ends.forEach(e=>{if(e.y-lastY>=13){const t=el('text',{x:m.l+iw+10,y:e.y+4},svg); t.textContent=e.label; lastY=e.y;}});
  const cross=el('line',{y1:m.t,y2:m.t+ih,class:'axis',visibility:'hidden'},svg);
  const dots=series.map(s=>el('circle',{r:4,fill:`var(${s.color})`,stroke:'var(--surface-1)','stroke-width':2,visibility:'hidden'},svg));
  const tip=host.parentElement.querySelector('.tip');
  const hit=el('rect',{x:m.l,y:m.t,width:iw,height:ih,fill:'transparent',tabindex:0},svg);
  function show(i){const cx=x(i); cross.setAttribute('x1',cx); cross.setAttribute('x2',cx); cross.setAttribute('visibility','visible');
    series.forEach((s,k)=>{dots[k].setAttribute('cx',cx); dots[k].setAttribute('cy',y(s.values[i])); dots[k].setAttribute('visibility','visible');});
    tip.replaceChildren(); const t=document.createElement('div'); t.className='t'; t.textContent=days[i]; tip.appendChild(t);
    [...series].sort((a,b)=>b.values[i]-a.values[i]).forEach(s=>tipRow(tip,`var(${s.color})`,s.label,fmt(s.values[i])));
    tip.style.display='block'; const left=cx+14+tip.offsetWidth>W? cx-14-tip.offsetWidth : cx+14; tip.style.left=left+'px'; tip.style.top=(m.t+8)+'px';}
  function hide(){cross.setAttribute('visibility','hidden'); dots.forEach(d=>d.setAttribute('visibility','hidden')); tip.style.display='none';}
  let cur=days.length-1;
  hit.addEventListener('pointermove',ev=>{const r=svg.getBoundingClientRect(); const px=(ev.clientX-r.left)*W/r.width;
    cur=Math.max(0,Math.min(days.length-1,Math.round((px-m.l)/iw*(days.length-1)))); show(cur);});
  hit.addEventListener('pointerleave',hide); hit.addEventListener('blur',hide); hit.addEventListener('focus',()=>show(cur));
  hit.addEventListener('keydown',ev=>{if(ev.key==='ArrowLeft')cur=Math.max(0,cur-1); else if(ev.key==='ArrowRight')cur=Math.min(days.length-1,cur+1); else return; show(cur); ev.preventDefault();});
})();

// ---------------------------------------------------------------- bot bars (verified / unverified / spoofed)
(function(){
  const host=document.getElementById('bots'); if(!host) return;
  const rows=D.bots, W=host.clientWidth||900, rowH=26, barH=16, m={l:150,r:70,t:4,b:4};
  const H=m.t+m.b+rows.length*rowH, iw=W-m.l-m.r;
  const svg=el('svg',{width:W,height:H,viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':'Requests by bot, split by IP verification'},host);
  const max=Math.max(...rows.map(r=>r.verified+r.unverified+r.spoofed));
  const segs=[['verified','--s1','Verified'],['unverified','--neutral','Not verifiable'],['spoofed','--critical','Spoofed']];
  const tip=host.parentElement.querySelector('.tip');
  el('line',{x1:m.l,x2:m.l,y1:m.t,y2:H-m.b,class:'axis'},svg);
  rows.forEach((r,i)=>{
    const cy=m.t+i*rowH+rowH/2; const t=el('text',{x:m.l-10,y:cy+4,'text-anchor':'end'},svg); t.textContent=r.bot;
    let x0=m.l; const total=r.verified+r.unverified+r.spoofed; const parts=segs.filter(s=>r[s[0]]>0);
    parts.forEach((s,k)=>{const w=r[s[0]]/max*iw; const last=k===parts.length-1; const gap=last?0:2; const ww=Math.max(1,w-gap);
      const p=el('path',{d:bar(x0,cy-barH/2,ww,barH,last?4:0),fill:`var(${s[1]})`,tabindex:0},svg);
      const g=el('rect',{x:x0,y:cy-rowH/2,width:Math.max(w,6),height:rowH,fill:'transparent'},svg);
      const showTip=ev=>{tip.replaceChildren(); const h=document.createElement('div'); h.className='t'; h.textContent=r.bot+' · '+fmt(total)+' requests'; tip.appendChild(h);
        segs.forEach(z=>tipRow(tip,`var(${z[1]})`,z[2],fmt(r[z[0]]),true)); tip.style.display='block';
        tip.style.left=Math.min(W-tip.offsetWidth-4, x0+w/2)+'px'; tip.style.top=(cy+rowH/2+2)+'px'; p.style.opacity=.8;};
      const hideTip=()=>{tip.style.display='none'; p.style.opacity=1;};
      [p,g].forEach(n=>{n.addEventListener('pointerenter',showTip); n.addEventListener('pointerleave',hideTip);});
      p.addEventListener('focus',showTip); p.addEventListener('blur',hideTip);
      x0+=w;});
    const v=el('text',{x:x0+8,y:cy+4},svg); v.textContent=fmt(total);
  });
  function bar(x,y,w,h,r){r=Math.min(r,w,h/2); return `M${x},${y}H${x+w-r}Q${x+w},${y} ${x+w},${y+r}V${y+h-r}Q${x+w},${y+h} ${x+w-r},${y+h}H${x}Z`;}
})();

document.querySelectorAll('.toggle').forEach(b=>b.addEventListener('click',()=>{const r=document.documentElement;
  const dark=r.dataset.theme? r.dataset.theme==='dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  r.dataset.theme=dark?'light':'dark';}));
"""


def _e(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    return html.escape(str(v))


def _table(rows: list[dict], cols: list[tuple[str, str]], limit: int | None = None, empty: str = "None.") -> str:
    rows = rows[:limit] if limit else rows
    if not rows:
        return f'<p class="note">{empty}</p>'
    num = {k for k, _ in cols if all(isinstance(r.get(k), int) and not isinstance(r.get(k), bool) or r.get(k) is None
                                      for r in rows)}
    head = "".join(f'<th class="{"n" if k in num else ""}">{html.escape(h)}</th>' for k, h in cols)
    body = "".join("<tr>" + "".join(f'<td class="{"n" if k in num else ""}">{_e(r.get(k))}</td>' for k, _ in cols)
                   + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _json_default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(o)


def render(R: dict) -> str:
    s = R["summary"]
    days = sorted({str(r["day"]) for r in R["daily_category"]})
    by = {(str(r["day"]), r["category"]): r["requests"] for r in R["daily_category"]}
    series = [{"key": c, "label": LABEL[c], "color": f"--s{i + 1}", "values": [by.get((d, c), 0) for d in days]}
              for i, c in enumerate(SERIES)]
    bots = [{"bot": b["bot"], "verified": b["verified"], "unverified": b["unverifiable"] + b["unchecked"],
             "spoofed": b["spoofed"]} for b in R["bots"]]
    bots.sort(key=lambda b: -(b["verified"] + b["unverified"] + b["spoofed"]))
    data = json.dumps({"days": days, "series": series, "bots": bots}, default=_json_default).replace("</", "<\\/")

    pct = lambda a, b: f"{100 * a / b:.0f}%" if b else "–"  # noqa: E731
    window = f"{s['first']:%b %d, %Y} – {s['last']:%b %d, %Y}" if s["first"] else "no data"
    never = len(R["never_ai"])
    parts: list[str] = []
    w = parts.append

    w(f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI crawler report</title><style>{CSS}</style></head><body><div class="viz-root"><main>
<button class="toggle" type="button">Light / dark</button>
<h1>AI crawler report</h1><p class="sub">{_e(window)} · {s['requests']:,} requests analysed</p>""")
    if R["synthetic_ranges"]:
        w('<div class="warn">Bot verification used synthetic IP ranges from the sample generator. '
          'Run <code>aicrawl ranges</code> to use the vendors\' published ranges.</div>')
    if R["unchecked_lists"]:
        w(f'<div class="warn">These IP lists weren\'t downloaded, so those bots weren\'t checked: '
          f'{_e(", ".join(R["unchecked_lists"]))}</div>')

    w('<div class="tiles">')
    for label, value, hint in [
        ("AI bot requests", f"{s['ai_genuine']:,}", f"{pct(s['ai_genuine'], s['requests'])} of all traffic"),
        ("Verified by IP", pct(s["ai_verified"], s["ai_genuine"]), f"{s['ai_verified']:,} from vendor IP ranges"),
        ("Spoofed requests", f"{s['spoofed_all']:,}", "bot user agent, wrong IP"),
        ("Pages no AI bot fetched", f"{never:,}", f"of {R['universe_size']:,} known pages"),
    ]:
        w(f'<div class="tile"><div class="label">{label}</div><div class="value">{value}</div>'
          f'<div class="hint">{hint}</div></div>')
    w("</div>")

    legend = "".join(f'<span><i class="key-line" style="background:var({x["color"]})"></i>{x["label"]}</span>'
                     for x in series if any(x["values"]))
    table_rows = [{"day": d, **{LABEL[c]: by.get((d, c), 0) for c in SERIES}} for d in days]
    w(f"""<section class="card"><h2>Crawl volume by type</h2>
<p class="note">Requests per day, spoofed traffic removed. Training crawlers collect data for future models, AI search
indexers build answer-engine indexes, user fetches happen live when someone asks an assistant about your page.</p>
<div class="legend">{legend}</div><div class="chart"><div id="daily"></div><div class="tip"></div></div>
<details><summary>Show as table</summary>{_table(table_rows, [("day", "day")] + [(LABEL[c], LABEL[c]) for c in SERIES])}</details>
</section>""")

    w(f"""<section class="card"><h2>Requests by bot</h2>
<p class="note">Each bar is everything that claimed to be that bot. Spoofed means the IP isn't in the vendor's
published ranges; not verifiable means the vendor publishes no way to check.</p>
<div class="legend"><span><i class="key-rect" style="background:var(--s1)"></i>Verified</span>
<span><i class="key-rect" style="background:var(--neutral)"></i>Not verifiable / unchecked</span>
<span><i class="key-rect" style="background:var(--critical)"></i>✖ Spoofed</span></div>
<div class="chart"><div id="bots"></div><div class="tip"></div></div>
<details><summary>Show as table</summary>{_table(R["bots"], [("bot", "bot"), ("vendor", "vendor"),
        ("category", "type"), ("requests", "requests (not spoofed)"), ("verified", "verified"),
        ("spoofed", "spoofed"), ("pages", "pages"), ("errors", "4xx/5xx"), ("robots_fetches", "robots.txt"),
        ("llms_fetches", "llms.txt"), ("last_seen", "last seen")])}</details></section>""")

    w('<section class="card"><h2>Top pages</h2><div class="cols">')
    for cat in AI_CATS:
        w(f'<div><h3>{LABEL[cat]}</h3>{_table(R["top_pages"][cat], [("path", "path"), ("requests", "bot hits"), ("human", "human views")], 12)}</div>')
    w("</div></section>")

    w(f"""<section class="card"><h2>Spoofed bot traffic</h2>
<p class="note">Scrapers often borrow a well-known bot's user agent to avoid being blocked.</p>
{_table(R["spoofing"], [("claimed", "claims to be"), ("requests", "requests"), ("ips", "IPs"), ("top_ips", "top IPs"), ("why", "why it failed")], empty="No spoofed requests found.")}
</section>""")

    rb = R["robots"]
    if rb:
        w('<section class="card"><h2>robots.txt</h2><p class="note">Checked with RFC 9309 rules: most specific '
          'group, longest match, Allow wins ties.</p><h3>Problems found</h3><ul class="lint">')
        for l in rb["lint"]:
            where = f"line {l['line']} · " if l["line"] else ""
            w(f'<li><span class="sev {l["severity"]}">{l["severity"]}</span>{where}{_e(l["message"])}</li>')
        if not rb["lint"]:
            w("<li>No problems found.</li>")
        w("</ul>")
        w("<h3>Popular pages Google may crawl but AI search or assistants may not</h3>")
        w(_table(rb["blocked_popular"], [("path", "path"), ("human", "human views"), ("blocked_for", "blocked for"),
                                         ("lines", "rule line")], empty="None. Popular pages are open to AI search."))
        w("<h3>Requests to disallowed URLs</h3>")
        w(_table([{**v, "who": "spoofed" if v["spoofed"] else v["verdict"]} for v in rb["violations"]],
                 [("bot", "bot"), ("who", "IP check"), ("requests", "requests"), ("rule", "rule"),
                  ("top_paths", "top paths")], empty="No bot fetched a disallowed URL."))
        w('<p class="note">User-triggered fetchers act for a person and vendors say they may not apply robots.txt '
          'the way crawlers do.</p>')
        policy = [{**p, "share": "—" if p["allowed"] is None else f"{p['allowed']}/{p['total']}"}
                  for p in rb["policy"] if p["named"] or p["category"] in AI_CATS]
        w("<details><summary>Policy per bot</summary>" + _table(policy, [("token", "token"), ("category", "type"),
          ("group", "group used"), ("share", "pages allowed"), ("content_usage", "Content-Usage")]) + "</details>")
        w("</section>")

    gaps = [g for g in R["gaps"] if g["category"] == "search"]
    w(f"""<section class="card"><h2>Coverage gaps</h2>
<p class="note">{R['universe_size']:,} known pages (pages people visited{', plus ' + str(R['sitemap_size']) + ' sitemap URLs' if R['sitemap_size'] else ''}).
{len(R['never_ai']):,} were never fetched by any AI bot. {R['gap_counts'].get('search', 0):,} are open to AI search indexers but
none fetched them, so they can't appear in AI answers.</p>
<div class="cols"><div><h3>Open to AI search, never fetched</h3>{_table(gaps, [("path", "path"), ("human", "human views"), ("in_sitemap", "in sitemap")], 15, "No gaps.")}</div>
<div><h3>Pages crawled vs allowed</h3>{_table(R['per_bot_coverage'], [("bot", "bot"), ("crawled", "crawled"), ("allowed", "allowed")])}</div></div>
</section>""")

    L = R["llms"]
    if L:
        fetched = ", ".join(f"{r['bot']} ×{r['n']}" for r in L["fetched_by"]) or "no AI bot"
        w(f"""<section class="card"><h2>llms.txt</h2>
<p class="note">{_e(L['title'] or '(no title)')} · {L['links']} links in {len(L['sections'])} sections · fetched by {_e(fetched)}</p>
<ul class="lint">{''.join(f'<li><span class="sev error">error</span>{_e(p)}</li>' for p in L['problems'])}
<li><span class="sev info">info</span>{len(L['linked_not_crawled'])} linked pages never fetched by an AI bot{(': ' + _e(', '.join(L['linked_not_crawled'][:6]))) if L['linked_not_crawled'] else ''}</li>
{''.join(f'<li><span class="sev error">error</span>Broken link: {_e(p)}</li>' for p in L['linked_missing'])}</ul></section>""")

    w(f'<script type="application/json" id="data">{data}</script><script>{JS}</script></main></div></body></html>')
    return "".join(parts)
