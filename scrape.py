#!/usr/bin/env python3
"""
빈또당 — 인스타그램 경품 이벤트 수집기

이벤트하우스(eventhouse.kr)의 '인스타그램 진행이벤트' 목록을 긁어서
정적 페이지(index.html)를 생성한다. GitHub Actions에서 매일 자동 실행된다.

분류는 전부 키워드 규칙 기반이라 가끔 틀린다. 규칙은 아래 상수만 고치면 된다.
"""

import html
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import requests

BASE = "https://www.eventhouse.kr"
LIST = BASE + "/site/instagram.php?view=1&page={}"
VIEW = BASE + "/site/view.php?no={}"
# 비회원 이용(flogin_no=10) + go=2 → 실제 인스타그램 게시물로 302
GO = BASE + "/site/fgo.php?fnum={}&go=2&flogin_no=10"
MAX_PAGES = 25
KST = timezone(timedelta(hours=9))

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# ─────────────────────────────────────────────────────────── 분류 규칙

# 고가 경품
RE_BIG = re.compile(
    r"노트북|맥북|아이패드|태블릿|아이폰|갤럭시|에어팟|버즈|스마트워치|갤럭시\s?워치|애플\s?워치|"
    r"TV\b|텔레비전|티브이|냉장고|세탁기|"
    r"건조기|에어컨|공기청정기|로봇청소기|안마의자|자전거|캠핑|텐트|여행|숙박|호텔|리조트|"
    r"항공|캐리어|카메라|드론|오즈모|프로젝터|스피커|헤드폰|이어폰|커피머신|에어프라이어|"
    r"정수기|스타일러|다이슨|런닝머신|러닝머신|트레드밀|백화점\s?상품권|"
    r"[1-9]\d만\s?원|\d{3}만\s?원|현금"
)

# 러닝·운동 — 경품 자체가 운동 관련인 경우
RE_RUN_PRIZE = re.compile(
    r"러닝화|런닝화|운동화|운동복|운동기구|트레드밀|런닝머신|러닝머신|헬스|피트니스|짐웨어|"
    r"요가|필라테스|홈트|덤벨|케틀벨|폼롤러|풀업|스포츠타월|아대|무릎보호대|압박|"
    r"스마트워치|갤럭시\s?워치|애플\s?워치|가민|스트라바|스마트밴드|체성분|인바디|"
    r"프로틴|단백질|보충제|쉐이커|이온음료|파워에이드|포카리|게토레이|BCAA|"
    r"자전거|라이딩|등산|하이킹|백패킹|수영|아쿠아|골프|테니스|배드민턴|풋살|클라이밍|"
    r"나이키|아디다스|아식스|호카|뉴발란스|언더아머|살로몬|브룩스|데카트론|"
    r"러닝|런닝|마라톤|조깅|트레일런"
)
# 러닝·운동 — 주최/제목이 러닝·운동 관련인 경우 (‘새마을운동’ 같은 오탐 방지용으로 좁게)
RE_RUN_HOST = re.compile(
    r"러닝|런닝|마라톤|러너|런너|조깅|트레일런|러닝크루|런닝크루|"
    r"헬스장|헬스클럽|피트니스|요가원|필라테스|트라이애슬론|철인|스포츠클럽|체육관"
)

# 손이 많이 가는 응모 (이게 없으면 '간편')
RE_HARD = re.compile(
    r"폼|제출|업로드|촬영|인증|구독|회원가입|스토리|숏폼|에세이|캡처|방문|다운로드|설치|"
    r"이메일|DM|영상|리그램|리포스트"
)

CAT_ICON = {
    "상품권": "🎟️", "기프티콘": "☕", "식료품": "🍱", "가전제품": "🔌",
    "생활용품": "🧺", "화장품": "💄", "패션잡화": "👜", "도서": "📚",
    "여행숙박": "🏨", "문화공연": "🎫",
}

# ─────────────────────────────────────────────────────────── 수집


SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": UA,
    "Referer": BASE + "/site/instagram.php?view=1",
    "Accept-Language": "ko-KR,ko;q=0.9",
})


def fetch(url, tries=3):
    for i in range(tries):
        try:
            r = SESSION.get(url, timeout=40)
            r.encoding = "utf-8"
            return r.text
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"  재시도 {i+1}/{tries}: {e}", file=sys.stderr)
            time.sleep(3)


def field(text, label, nxt):
    m = re.search(rf"ㆍ{label}\s*(.*?)\s*(?:ㆍ{nxt}|$)", text)
    return m.group(1).strip() if m else ""


def parse_page(raw):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw, "lxml")
    out = []
    for det in soup.select(".list_detail"):
        row = det.find_previous_sibling("div", class_="list_row")
        if not row:
            continue

        rtext = re.sub(r"\s+", " ", row.get_text(" ")).strip()
        dtext = re.sub(r"\s+", " ", det.get_text(" ")).strip()

        a = det.select_one('a[href*="fgo"]') or row.select_one('a[href*="view.php"]')
        m = re.search(r"(\d{5,})", a.get("href")) if a else None
        if not m:
            continue
        eid = m.group(1)

        title = ""
        mt = re.search(r"\]\s*(.*?)\s*(?:이벤트!!|경품이벤트!!)", rtext)
        if mt:
            title = mt.group(1).strip()

        cat = ""
        mc = re.search(r"(\d{2}/\d{2})\s+(\S+)\s*$", rtext)
        if mc:
            cat = mc.group(2)

        period = field(dtext, "응모기간", "발표일정")
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", period)
        if len(dates) < 2:
            continue

        prize = field(dtext, "경품내역", "응모기간")
        prize = re.sub(r"\(포함 총\)\s*([\d,]+)\s*명", r"외 총 \1명", prize).strip()

        how = field(dtext, "응모방법", "$^")
        how = re.split(r"💡", how)[0]
        how = re.sub(r"\s*[①②③④⑤⑥]\s*", " → ", how).strip(" →").strip()

        # 상세 경품 목록 (list_detail 앞부분에 있는 경우)
        head = dtext.split("ㆍ주최회사")[0].strip()
        prizes = head if head and len(head) < 300 else ""

        host = field(dtext, "주최회사", "응모형태")
        etype = field(dtext, "응모형태", "경품내역")
        blob = " ".join([host, title, prize, prizes, cat])
        prize_blob = " ".join([prize, prizes])

        out.append({
            "id": eid,
            "host": host,
            "title": title,
            "type": etype,
            "cat": cat,
            "prize": prize,
            "prizes": prizes,
            "start": dates[0],
            "end": dates[1],
            "announce": field(dtext, "발표일정", "응모현황"),
            "how": how,
            "big": bool(RE_BIG.search(prize_blob)),
            "run": bool(RE_RUN_PRIZE.search(prize_blob)
                        or RE_RUN_HOST.search(host + " " + title)),
            "easy": not bool(RE_HARD.search(how)),
        })
    return out


def collect():
    seen, rows = set(), []
    for p in range(1, MAX_PAGES + 1):
        print(f"page {p} …", file=sys.stderr)
        page = parse_page(fetch(LIST.format(p)))
        if not page:
            break
        fresh = [e for e in page if e["id"] not in seen]
        for e in fresh:
            seen.add(e["id"])
        rows.extend(fresh)
        if len(fresh) == 0:
            break
        time.sleep(0.6)
    return rows


def load_cache():
    """이전 실행에서 이미 확인한 인스타그램 주소를 재사용한다."""
    if not os.path.exists("data.json"):
        return {}
    try:
        with open("data.json", encoding="utf-8") as f:
            old = json.load(f)
        return {e["id"]: e["url"] for e in old.get("events", []) if e.get("url")}
    except Exception as e:
        print(f"캐시 로드 실패: {e}", file=sys.stderr)
        return {}


def resolve_urls(rows, cache):
    """fgo.php 리다이렉트를 따라가 실제 인스타그램 게시물 주소를 얻는다."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": BASE + "/site/instagram.php?view=1"})
    try:
        s.get(BASE + "/site/instagram.php?view=1", timeout=30)
    except Exception as e:
        print(f"세션 준비 실패: {e}", file=sys.stderr)

    hit = miss = 0
    for e in rows:
        if e["id"] in cache:
            e["url"] = cache[e["id"]]
            hit += 1
            continue
        e["url"] = ""
        try:
            r = s.get(GO.format(e["id"]), timeout=25, allow_redirects=False)
            loc = r.headers.get("Location", "")
            if "instagram.com" in loc:
                e["url"] = loc.replace("http://", "https://")
        except Exception:
            pass
        miss += 1
        time.sleep(0.35)
    print(f"인스타 주소: 캐시 {hit}건 / 신규 조회 {miss}건", file=sys.stderr)
    return rows

# ─────────────────────────────────────────────────────────── 렌더링

PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>빈또당 — 인스타 이벤트 응모 리스트</title>
<meta name="description" content="지금 응모 가능한 인스타그램 경품 이벤트를 매일 자동으로 모아둔 목록. 마감일순 정리, 게시물 바로가기.">
<link rel="icon" type="image/svg+xml" href="favicon.svg">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<meta name="theme-color" content="#1f7a4d">
<meta property="og:type" content="website">
<meta property="og:site_name" content="빈또당">
<meta property="og:title" content="빈또당 — 인스타 경품 이벤트 모음">
<meta property="og:description" content="지금 응모 가능한 이벤트만, 마감일순으로 매일 자동 갱신.">
<meta property="og:url" content="https://spacehwi.github.io/binttodang/">
<meta property="og:image" content="https://spacehwi.github.io/binttodang/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="네잎클로버 로고와 빈또당">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="빈또당 — 인스타 경품 이벤트 모음">
<meta name="twitter:description" content="지금 응모 가능한 이벤트만, 마감일순으로 매일 자동 갱신.">
<meta name="twitter:image" content="https://spacehwi.github.io/binttodang/og.png">
<style>
:root{
  --bg:#f7faf8; --card:#fff; --ink:#16241c; --mut:#63756b; --line:#dfe8e2;
  --accent:#1f7a4d; --accent-2:#2e9c64; --soft:#e8f3ec; --urgent:#b8442f; --gold:#8a6d1f;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",Pretendard,"Noto Sans KR",sans-serif;
 font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased}
a{color:var(--accent)}
.wrap{max-width:1060px;margin:0 auto;padding:30px 18px 80px}
header{border-bottom:2px solid var(--accent);padding-bottom:16px}
.brand{display:flex;align-items:center;gap:11px;flex-wrap:wrap}
.logo{flex:none;margin-bottom:2px}
h1{font-size:30px;margin:0;letter-spacing:-.03em;color:var(--accent)}
.sub{font-size:13px;color:var(--mut)}
.upd{font-size:12px;color:var(--mut);margin-top:6px}
#stale{margin-top:16px;padding:11px 14px;border-radius:10px;font-size:13.5px;
 background:#fdf1ea;border:1px solid #f0cdbb;color:#8a3d26}
.stats{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0 4px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:9px 14px;min-width:88px}
.stat b{display:block;font-size:19px;letter-spacing:-.02em;color:var(--accent)}
.stat span{font-size:11.5px;color:var(--mut)}
.controls{position:sticky;top:0;z-index:5;background:var(--bg);padding:12px 0 10px;
 border-bottom:1px solid var(--line)}
.rowc{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.rowc+.rowc{margin-top:7px}
.lbl{font-size:11.5px;color:var(--mut);margin:0 3px 0 6px}
button.f{border:1px solid var(--line);background:var(--card);color:var(--ink);
 padding:5px 11px;border-radius:999px;font-size:12.5px;cursor:pointer;font-family:inherit}
button.f:hover{border-color:var(--accent-2)}
button.f.on{background:var(--accent);border-color:var(--accent);color:#fff}
input[type=search]{flex:1;min-width:170px;padding:6px 11px;border:1px solid var(--line);
 border-radius:8px;background:var(--card);font-family:inherit;font-size:13.5px;color:var(--ink)}
.daygroup{margin-top:22px}
.dayhead{display:flex;align-items:baseline;gap:9px;margin-bottom:7px}
.dayhead h2{font-size:15px;margin:0;letter-spacing:-.01em}
.dayhead .d{font-size:11.5px;color:var(--mut)}
.dayhead .u{font-size:10.5px;color:#fff;background:var(--urgent);padding:2px 7px;border-radius:999px}
.item{display:grid;grid-template-columns:24px 1fr auto;gap:11px;align-items:start;
 background:var(--card);border:1px solid var(--line);border-radius:10px;padding:11px 13px;margin-bottom:6px}
.item.done{opacity:.4}
.item.done .name{text-decoration:line-through}
.chk{width:17px;height:17px;margin-top:2px;accent-color:var(--accent);cursor:pointer}
.name{font-weight:600;letter-spacing:-.01em;font-size:14.5px}
.tag{font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;margin-left:5px;
 vertical-align:2px;letter-spacing:0}
.t-big{color:var(--gold);background:#f7f0dc}
.t-run{color:#fff;background:var(--accent-2)}
.t-easy{color:var(--accent);background:var(--soft)}
.prize{font-size:13.5px;margin-top:2px}
.how{font-size:12.5px;color:var(--mut);margin-top:3px}
.acts{display:flex;flex-direction:column;align-items:flex-end;gap:4px}
.go{white-space:nowrap;font-size:12.5px;text-decoration:none;color:#fff;font-weight:600;
 border:1px solid var(--accent);border-radius:7px;padding:6px 11px;background:var(--accent)}
.go:hover{background:var(--accent-2);border-color:var(--accent-2)}
.sub2{font-size:11px;color:var(--mut);text-decoration:none}
.sub2:hover{text-decoration:underline;color:var(--accent)}
.empty{color:var(--mut);padding:34px 0}
footer{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);
 font-size:12.5px;color:var(--mut);line-height:1.7}
@media(max-width:620px){
 h1{font-size:25px}
 .item{grid-template-columns:22px 1fr}
 .acts{grid-column:2;flex-direction:row;align-items:center;gap:10px;margin-top:6px}
}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="brand"><svg class="logo" viewBox="0 0 100 100" width="42" height="42" aria-hidden="true"><path d="M50 62 C 53 73, 58 81, 68 87" fill="none" stroke="#15613c" stroke-width="5.5" stroke-linecap="round"/><g transform="translate(50 49)" fill="#2e9c64"><path transform="rotate(45)" d="M0 0 C-9.9 -8 -16 -16.7 -16 -25.8 C-16 -35.3 -8.8 -38.8 -3.8 -34.2 C-1.8 -32.3 -0.8 -32 0 -32 C0.8 -32 1.8 -32.3 3.8 -34.2 C8.8 -38.8 16 -35.3 16 -25.8 C16 -16.7 9.9 -8 0 0 Z"/><path transform="rotate(135)" d="M0 0 C-9.9 -8 -16 -16.7 -16 -25.8 C-16 -35.3 -8.8 -38.8 -3.8 -34.2 C-1.8 -32.3 -0.8 -32 0 -32 C0.8 -32 1.8 -32.3 3.8 -34.2 C8.8 -38.8 16 -35.3 16 -25.8 C16 -16.7 9.9 -8 0 0 Z"/><path transform="rotate(225)" d="M0 0 C-9.9 -8 -16 -16.7 -16 -25.8 C-16 -35.3 -8.8 -38.8 -3.8 -34.2 C-1.8 -32.3 -0.8 -32 0 -32 C0.8 -32 1.8 -32.3 3.8 -34.2 C8.8 -38.8 16 -35.3 16 -25.8 C16 -16.7 9.9 -8 0 0 Z"/><path transform="rotate(315)" d="M0 0 C-9.9 -8 -16 -16.7 -16 -25.8 C-16 -35.3 -8.8 -38.8 -3.8 -34.2 C-1.8 -32.3 -0.8 -32 0 -32 C0.8 -32 1.8 -32.3 3.8 -34.2 C8.8 -38.8 16 -35.3 16 -25.8 C16 -16.7 9.9 -8 0 0 Z"/></g></svg><h1>빈또당</h1><span class="sub">빈손으로 또 당첨 — 인스타 이벤트 응모 리스트</span></div>
  <div class="upd">마지막 갱신 __UPDATED__ · 매일 아침 자동 갱신</div>
</header>
<div id="stale" hidden></div>

<div class="stats">
  <div class="stat"><b id="s-all">0</b><span>진행 중</span></div>
  <div class="stat"><b id="s-big">0</b><span>고가 경품</span></div>
  <div class="stat"><b id="s-run">0</b><span>런닝·운동</span></div>
  <div class="stat"><b id="s-week">0</b><span>일주일 내 마감</span></div>
  <div class="stat"><b id="s-done">0</b><span>응모 완료</span></div>
</div>

<div class="controls">
  <div class="rowc">
    <span class="lbl" style="margin-left:0">분류</span>
    <button class="f on" data-t="kind" data-v="all">전체</button>
    <button class="f" data-t="kind" data-v="big">고가 경품</button>
    <button class="f" data-t="kind" data-v="run">런닝·운동</button>
    <button class="f" data-t="kind" data-v="easy">간편 (댓글만)</button>
  </div>
  <div class="rowc">
    <span class="lbl" style="margin-left:0">경품</span>
    <button class="f on" data-t="cat" data-v="all">전체</button>
    __CATBTNS__
  </div>
  <div class="rowc">
    <span class="lbl" style="margin-left:0">마감</span>
    <button class="f on" data-t="due" data-v="all">전체</button>
    <button class="f" data-t="due" data-v="1">내일까지</button>
    <button class="f" data-t="due" data-v="3">3일 내</button>
    <button class="f" data-t="due" data-v="7">일주일 내</button>
    <input type="search" id="q" placeholder="브랜드·경품 검색 (예: 러닝화, 상품권, 치킨)">
    <button class="f" id="hidedone">완료 숨기기</button>
  </div>
</div>

<div id="list"></div>

<footer>
  <p><b>인스타 게시물</b> 버튼을 누르면 해당 이벤트 게시물로 바로 이동합니다. 경품 구성이나 발표일 같은 원문 정보는 옆의 ‘원문 정보’ 링크에서 볼 수 있습니다. 체크 표시는 이 브라우저에만 저장됩니다.</p>
  <p>‘고가 / 런닝·운동 / 간편’ 분류는 키워드 규칙으로 자동 판별하므로 간혹 부정확할 수 있습니다. 경품·마감·응모방법 원문은 <a href="https://www.eventhouse.kr/site/instagram.php?view=1" target="_blank" rel="noopener">이벤트하우스</a>에서 확인하세요.</p>
  <p>데이터 출처: 이벤트하우스 · 개인 용도로 매일 1회 수집 · 문의는 저장소 이슈로.</p>
</footer>
</div>

<script>
const DATA = __DATA__;
// 날짜는 보는 사람의 시간대와 무관하게 같은 날짜로 읽히도록 로컬 자정으로 만든다
const dd = s => { const [y,m,d] = s.split("-").map(Number); return new Date(y, m-1, d); };
const TODAY = dd("__TODAY__");
const left = s => Math.round((dd(s) - TODAY) / 86400000);
const WD = ["일","월","화","수","목","금","토"];
const esc = s => (s||"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

let store = {};
try { store = JSON.parse(localStorage.getItem("bd_done") || "{}"); } catch(e) { store = {}; }
const save = () => { try { localStorage.setItem("bd_done", JSON.stringify(store)); } catch(e){} };

const state = {kind:"all", cat:"all", due:"all", q:"", hideDone:false};

function ok(e){
  if(state.kind === "big"  && !e.big)  return false;
  if(state.kind === "run"  && !e.run)  return false;
  if(state.kind === "easy" && !e.easy) return false;
  if(state.cat !== "all" && e.cat !== state.cat) return false;
  if(state.due !== "all" && left(e.end) > +state.due) return false;
  if(state.q && !(e.host + e.title + e.prize + e.how).toLowerCase().includes(state.q)) return false;
  if(state.hideDone && store[e.id]) return false;
  return true;
}

function render(){
  const rows = DATA.filter(ok).sort((a,b) => a.end < b.end ? -1 : a.end > b.end ? 1 : (b.big - a.big));
  const el = document.getElementById("list");
  if(!rows.length){ el.innerHTML = '<p class="empty">조건에 맞는 이벤트가 없습니다.</p>'; stats(); return; }
  let h = "", last = "";
  for(const e of rows){
    if(e.end !== last){
      if(last) h += "</div>";
      last = e.end;
      const n = left(e.end), d = dd(e.end);
      h += '<div class="daygroup"><div class="dayhead">'
         + '<h2>' + (d.getMonth()+1) + '월 ' + d.getDate() + '일 (' + WD[d.getDay()] + ') 마감</h2>'
         + '<span class="d">' + (n <= 0 ? "오늘" : "D-" + n) + '</span>'
         + (n <= 2 ? '<span class="u">임박</span>' : '') + '</div>';
    }
    h += '<div class="item' + (store[e.id] ? " done" : "") + '" data-id="' + e.id + '">'
       + '<input type="checkbox" class="chk"' + (store[e.id] ? " checked" : "") + '>'
       + '<div><div class="name">' + esc(e.host)
       + (e.big  ? '<span class="tag t-big">고가</span>'  : "")
       + (e.run  ? '<span class="tag t-run">런닝·운동</span>' : "")
       + (e.easy ? '<span class="tag t-easy">간편</span>' : "")
       + '</div><div class="prize">' + esc(e.prize) + '</div>'
       + '<div class="how">' + esc(e.how) + '</div></div>'
       + '<div class="acts">'
       + (e.url
          ? '<a class="go" href="' + e.url + '" target="_blank" rel="noopener">인스타 게시물 →</a>'
          : '<a class="go" href="https://www.eventhouse.kr/site/view.php?no=' + e.id
            + '" target="_blank" rel="noopener">상세 보기 →</a>')
       + '<a class="sub2" href="https://www.eventhouse.kr/site/view.php?no=' + e.id
       + '" target="_blank" rel="noopener">원문 정보</a>'
       + '</div></div>';
  }
  el.innerHTML = h + "</div>";
  el.querySelectorAll(".chk").forEach(c => c.onchange = ev => {
    const id = ev.target.closest(".item").dataset.id;
    if(ev.target.checked) store[id] = 1; else delete store[id];
    save(); render();
  });
  stats();
}

function stats(){
  document.getElementById("s-all").textContent  = DATA.length;
  document.getElementById("s-big").textContent  = DATA.filter(e => e.big).length;
  document.getElementById("s-run").textContent  = DATA.filter(e => e.run).length;
  document.getElementById("s-week").textContent = DATA.filter(e => left(e.end) <= 7).length;
  document.getElementById("s-done").textContent = DATA.filter(e => store[e.id]).length;
}

document.querySelectorAll("button.f[data-t]").forEach(b => b.onclick = () => {
  const t = b.dataset.t;
  document.querySelectorAll('button.f[data-t="' + t + '"]').forEach(x => x.classList.remove("on"));
  b.classList.add("on"); state[t] = b.dataset.v; render();
});
document.getElementById("q").oninput = e => { state.q = e.target.value.trim().toLowerCase(); render(); };
document.getElementById("hidedone").onclick = e => {
  state.hideDone = !state.hideDone;
  e.target.classList.toggle("on", state.hideDone);
  e.target.textContent = state.hideDone ? "완료 표시하기" : "완료 숨기기";
  render();
};
// 자동 갱신이 멈췄을 때(수집원 차단·구조 변경 등) 오래된 정보임을 알린다
(function () {
  const now = new Date();
  const days = Math.floor((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - TODAY) / 86400000);
  if (days >= 2) {
    const el = document.getElementById("stale");
    el.hidden = false;
    el.innerHTML = "⚠️ 이 목록은 <b>" + days + "일 전</b> 기준입니다. 자동 갱신이 멈춘 상태일 수 있으니 "
      + "마감일이 지난 이벤트가 섞여 있을 수 있어요. "
      + '<a href="https://github.com/spaceHWI/binttodang/actions" target="_blank" rel="noopener">갱신 기록 확인</a>';
  }
})();

render();
</script>
</body>
</html>
"""


def build(rows, today):
    rows = [e for e in rows if e["end"] >= today]
    rows.sort(key=lambda e: (e["end"], not e["big"]))

    cats, seen = [], set()
    for e in rows:
        c = e["cat"]
        if c and c not in seen:
            seen.add(c)
            cats.append((c, sum(1 for x in rows if x["cat"] == c)))
    cats.sort(key=lambda x: -x[1])
    btns = "\n    ".join(
        f'<button class="f" data-t="cat" data-v="{html.escape(c)}">'
        f'{CAT_ICON.get(c,"")} {html.escape(c)}</button>'
        for c, _ in cats[:8]
    )

    slim = [{k: e[k] for k in
             ("id", "host", "prize", "how", "end", "cat", "big", "run", "easy", "url")}
            for e in rows]

    now = datetime.now(KST).strftime("%Y년 %m월 %d일 %H:%M")
    return (PAGE
            .replace("__DATA__", json.dumps(slim, ensure_ascii=False, separators=(",", ":")))
            .replace("__TODAY__", today)
            .replace("__UPDATED__", now)
            .replace("__CATBTNS__", btns))


def main():
    today = datetime.now(KST).strftime("%Y-%m-%d")
    cache = load_cache()
    rows = collect()
    print(f"수집 {len(rows)}건", file=sys.stderr)
    if len(rows) < 20:
        print("수집 결과가 너무 적습니다. 기존 페이지를 유지합니다.", file=sys.stderr)
        return 1
    rows = [e for e in rows if e["end"] >= today]
    resolve_urls(rows, cache)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(build(rows, today))
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump({"updated": today, "count": len(rows), "events": rows},
                  f, ensure_ascii=False, indent=1)
    print("index.html 생성 완료", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
