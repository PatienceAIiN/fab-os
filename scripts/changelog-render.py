#!/usr/bin/env python3
"""Render the public Fab OS changelog from docs/CHANGELOG.md — the single source of truth.

Outputs (deterministic: the same input always gives byte-identical output):
  website/changelog/index.html   the changelog page, built with the header, footer and stylesheet of website/index.html
                                 so it looks native; releases newest first, each with its date, a kind badge, its bullets,
                                 a "Known limits" line when present and a "How to get it" line
  website/updates.xml            the RSS feed: the channel header of the existing file is kept, the <item>s are regenerated
                                 (one per published release, keyed by version — re-running never duplicates)
  --notes REV                    prints the Markdown "what changed" body of one release for scripts/release-github.sh
  --info REV                     prints shell-sourceable facts about one entry (CL_TAG, CL_KIND, CL_DATE, CL_STATUS, CL_VERSION)
  --list                         prints one line per entry

Usage:
  scripts/changelog-render.py                       # write website/changelog/index.html + website/updates.xml
  scripts/changelog-render.py --out-dir /tmp/x      # write /tmp/x/changelog/index.html + /tmp/x/updates.xml (the gate diffs them)
  scripts/changelog-render.py --notes 7             # Markdown notes for package revision 1.0-7
  scripts/changelog-render.py --info 7              # CL_TAG=v1.0.6 CL_KIND=ota ...

Entry format (docs/CHANGELOG.md), newest first:
  <!-- release: rev=7 tag=v1.0.6 kind=ota date=2026-09-16 time=18:16 -->
  ## 1.0-7 — 16 September 2026 (over-the-air)
  optional short paragraph
  - bullet
  Known limits: optional paragraph
Fields: rev (package revision, integer), tag (must equal v<version>.<rev-1>), kind (image | ota | withheld),
date (YYYY-MM-DD, or "unreleased" for a draft), time (HH:MM UTC, default 12:00 — the feed needs a full timestamp),
file (the disc image file under /download/ for the current image release; older images link to the home page),
status=draft (kept out of the page, the feed and GitHub). Drafts and withheld entries never enter the feed.
kind=withheld means the image was not published; its paragraph must say what installed systems received if the update
channel carried the revision (1.0-5 did reach installed systems for a day). No GitHub link is rendered for it.
"""
import argparse
import datetime
import html
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE_URL = "https://fabos.patienceai.in"
GITHUB_REPO = "PatienceAIiN/fab-os"
KINDS = {"image": "Image", "ota": "Over-the-air update", "withheld": "Image withheld"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
HEADER_RE = re.compile(r"<!--\s*release:\s*([^>]*?)\s*-->")
FIELD_RE = re.compile(r"([A-Za-z_]+)=(\S+)")
HEADING_RE = re.compile(r"^## (\d+\.\d+)-(\d+) — (.+)$")
# extra rules for the changelog page; everything else comes from website/index.html's stylesheet
PAGE_SIZE = 5   # releases per page on the website

PAGE_CSS = """
.pager{display:flex;align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;margin:34px 0 0}.pager[hidden]{display:none}
.pager button{border:1.5px solid var(--plum);background:#fff;color:var(--plum);border-radius:999px;padding:10px 18px;font:inherit;font-weight:700;cursor:pointer}
.pager button[aria-current="page"]{background:var(--plum);color:#fff}.pager button:disabled{opacity:.35;cursor:default}.pager .pager-status{color:#75457f;font-size:14px;margin:0 6px}
.release[data-page-hidden]{display:none}
.changelog-hero{padding:172px 0 58px;background:linear-gradient(112deg,#f2c4dd 0%,#f8d8e8 44%,#fcecf0 100%)}
.changelog-hero .section-head{margin-bottom:0;max-width:720px}.changelog-hero .section-head p{color:#75457f}
.changelog-hero .section-head p+p{margin-top:10px}.changelog-hero a{color:var(--plum);font-weight:700;text-decoration:underline}
.changelog-list{display:grid;gap:22px}
.release{padding:30px 34px;border:1px solid #e8d7e6;border-radius:var(--radius);background:#fff;box-shadow:0 10px 36px rgba(101,52,116,.06)}
.release-meta{display:flex;flex-wrap:wrap;align-items:center;gap:12px;font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#8b4c95}
.release-badge{display:inline-flex;align-items:center;padding:5px 11px;border-radius:999px;background:var(--pink);color:var(--plum)}
.release-badge.ota{background:#e7d8f0}.release-badge.withheld{background:#f1ece4;color:#7a6650}
.release-tag{color:#a568aa}
.release h3{font-size:30px;margin:12px 0 8px;color:var(--deep)}
.release p{margin:8px 0 0;color:#7c5a81;font-size:15px}
.release ul{margin:14px 0 0;padding-left:22px;display:grid;gap:8px;color:#5f3d68;font-size:15px}
.release-limits{margin-top:16px!important;padding:14px 16px;border-radius:14px;background:#fbf3f7;font-size:14px!important}
.release-get{margin-top:14px!important;font-size:14px!important}.release-links{margin-top:6px!important;font-size:13px!important}
.release-get a,.release-links a{color:var(--plum);font-weight:700;text-decoration:underline}
@media(max-width:560px){.changelog-hero{padding:132px 0 40px}.release{padding:22px 20px;border-radius:20px}.release h3{font-size:26px}}
"""


class ChangelogError(Exception):
    pass


class Release:
    def __init__(self, fields, heading_line, body_lines, position):
        self.position = position
        try:
            self.rev = int(fields["rev"])
        except (KeyError, ValueError):
            raise ChangelogError("header %d: rev=<integer> is required" % position)
        self.tag = fields.get("tag", "")
        self.kind = fields.get("kind", "")
        self.date = fields.get("date", "")
        self.time = fields.get("time", "12:00")
        self.file = fields.get("file", "")
        self.status = fields.get("status", "released")
        m = HEADING_RE.match(heading_line)
        if not m:
            raise ChangelogError("entry rev=%d: the heading must read '## <version>-<rev> — <words>', got %r"
                                 % (self.rev, heading_line))
        self.version = m.group(1)
        if int(m.group(2)) != self.rev:
            raise ChangelogError("entry rev=%d: heading says revision %s" % (self.rev, m.group(2)))
        self.heading_words = m.group(3)
        if self.kind not in KINDS:
            raise ChangelogError("entry %s: kind must be one of %s" % (self.pkgver, ", ".join(sorted(KINDS))))
        if self.status not in ("released", "draft"):
            raise ChangelogError("entry %s: status may only be 'draft'" % self.pkgver)
        expected_tag = "v%s.%d" % (self.version, self.rev - 1)
        if self.tag != expected_tag:
            raise ChangelogError("entry %s: tag=%s but the formula v<version>.<rev-1> gives %s"
                                 % (self.pkgver, self.tag or "(none)", expected_tag))
        if self.date == "unreleased":
            if self.status != "draft":
                raise ChangelogError("entry %s: date=unreleased is only allowed on a draft" % self.pkgver)
        else:
            try:
                self.day = datetime.date.fromisoformat(self.date)
            except ValueError:
                raise ChangelogError("entry %s: date must be YYYY-MM-DD or 'unreleased'" % self.pkgver)
        if not re.fullmatch(r"[0-2]\d:[0-5]\d", self.time):
            raise ChangelogError("entry %s: time must be HH:MM (UTC)" % self.pkgver)
        self.intro, self.bullets, self.after, self.limits = [], [], [], None
        self._parse_body(body_lines)

    def _parse_body(self, lines):
        paragraphs, cur = [], []
        for ln in lines:
            if ln.strip() == "":
                if cur:
                    paragraphs.append(cur)
                    cur = []
            else:
                cur.append(ln)
        if cur:
            paragraphs.append(cur)
        seen_list = False
        for para in paragraphs:
            if para[0].startswith("- "):
                if seen_list:
                    raise ChangelogError("entry %s: one bullet list per release" % self.pkgver)
                seen_list = True
                item = None
                for ln in para:
                    if ln.startswith("- "):
                        if item is not None:
                            self.bullets.append(item)
                        item = ln[2:].strip()
                    else:
                        item = (item or "") + " " + ln.strip()
                self.bullets.append(item)
                continue
            text = " ".join(x.strip() for x in para)
            if text.startswith("Known limits:"):
                if self.limits is not None:
                    raise ChangelogError("entry %s: one 'Known limits:' paragraph per release" % self.pkgver)
                self.limits = text[len("Known limits:"):].strip()
            elif seen_list:
                self.after.append(text)
            else:
                self.intro.append(text)
        if self.status != "draft" and self.kind != "withheld" and not (4 <= len(self.bullets) <= 10):
            raise ChangelogError("entry %s: a published release needs 4-10 bullets, has %d"
                                 % (self.pkgver, len(self.bullets)))

    @property
    def pkgver(self):
        return "%s-%d" % (self.version, self.rev)

    @property
    def anchor(self):
        return self.pkgver

    @property
    def draft(self):
        return self.status == "draft"

    @property
    def published(self):
        return not self.draft and self.kind != "withheld"

    @property
    def human_date(self):
        if self.date == "unreleased":
            return "not yet released"
        return "%d %s %d" % (self.day.day, MONTHS[self.day.month - 1], self.day.year)

    @property
    def pub_date(self):
        hh, mm = self.time.split(":")
        dt = datetime.datetime(self.day.year, self.day.month, self.day.day, int(hh), int(mm))
        return "%s, %02d %s %d %02d:%02d:00 +0000" % (DAYS[dt.weekday()], dt.day, MONTHS[dt.month - 1][:3], dt.year,
                                                       dt.hour, dt.minute)

    @property
    def kind_words(self):
        if self.kind == "image":
            return "image %s" % self.tag
        return KINDS[self.kind].lower()

    @property
    def github_url(self):
        return "https://github.com/%s/releases/tag/%s" % (GITHUB_REPO, self.tag)

    def get_line(self, fmt, rel):
        """The 'How to get it' sentence. fmt = html | text | md; rel = prefix for site-relative links in html."""
        if self.kind == "withheld":
            return ""
        if self.kind == "ota":
            text = "Installed systems receive it automatically through Fab Updates."
            return text
        if self.file:
            href_rel, href_abs = rel + "download/" + self.file, SITE_URL + "/download/" + self.file
            words, tail = "Get the disc image", " and install it; systems already installed receive these packages through Fab Updates."
        else:
            href_rel, href_abs = rel + "#status", SITE_URL + "/#status"
            words, tail = "This image has been superseded — get the current disc image", " instead; systems already installed receive these packages through Fab Updates."
        if fmt == "html":
            return '<a href="%s">%s</a>%s' % (href_rel, words, tail)
        if fmt == "md":
            return "[%s](%s)%s" % (words, href_abs, tail)
        return "%s (%s)%s" % (words, href_abs, tail)


def read_brand():
    conf = {}
    with open(os.path.join(ROOT, "brand", "brand.conf"), encoding="utf-8") as fh:
        for ln in fh:
            m = re.match(r'^([A-Z_]+)="?([^"]*)"?\s*$', ln.strip())
            if m:
                conf[m.group(1)] = m.group(2)
    return conf


def parse_changelog(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    heads = list(HEADER_RE.finditer(text))
    if not heads:
        raise ChangelogError("%s: no '<!-- release: ... -->' headers found" % path)
    releases = []
    for i, m in enumerate(heads):
        fields = dict(FIELD_RE.findall(m.group(1)))
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        block = re.sub(r"<!--.*?-->", "", text[m.end():end], flags=re.S)
        lines = block.split("\n")
        while lines and lines[0].strip() == "":
            lines.pop(0)
        if not lines or not lines[0].startswith("## "):
            raise ChangelogError("header %d (%s): a '## ' heading must follow the release header" % (i + 1, m.group(1)))
        releases.append(Release(fields, lines[0].rstrip(), lines[1:], i + 1))
    revs = [r.rev for r in releases]
    if len(set(revs)) != len(revs):
        raise ChangelogError("a package revision appears twice: %s" % sorted(set(x for x in revs if revs.count(x) > 1)))
    if revs != sorted(revs, reverse=True):
        raise ChangelogError("entries must be newest first (by rev); found order %s" % revs)
    versions = set(r.version for r in releases)
    if len(versions) != 1:
        raise ChangelogError("all entries must share one Fab OS version; found %s" % sorted(versions))
    return releases


# ---- inline text ----------------------------------------------------------------------------------------------------
def inline_html(md):
    s = html.escape(md, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return s


def inline_text(md):
    return re.sub(r"\*\*(.+?)\*\*", r"\1", md)


# ---- the page -------------------------------------------------------------------------------------------------------
def slice_between(text, start, end, what):
    a = text.find(start)
    if a < 0:
        raise ChangelogError("website/index.html: cannot find %s (%r)" % (what, start))
    b = text.find(end, a)
    if b < 0:
        raise ChangelogError("website/index.html: unterminated %s" % what)
    return text[a:b + len(end)]


def relink(fragment):
    """index.html sits at the site root; the changelog page sits one directory down."""
    def fix(m):
        attr, url = m.group(1), m.group(2)
        if url.startswith(("http://", "https://", "mailto:", "/", "../")):
            new = url
        elif url.startswith("#"):
            new = "../" + url
        elif url == "changelog/":
            new = "./"
        else:
            new = "../" + url
        return '%s="%s"' % (attr, new)
    return re.sub(r'(href|src)="([^"]*)"', fix, fragment)


def render_release_html(r):
    out = ['<article class="release" id="%s">' % html.escape(r.anchor)]
    meta = ['<span class="release-badge %s">%s</span>' % (r.kind, KINDS[r.kind]),
            '<span class="release-date">%s</span>' % r.human_date]
    if r.kind != "withheld":
        meta.append('<span class="release-tag">%s</span>' % html.escape(r.tag))
    out.append('<div class="release-meta">%s</div>' % "".join(meta))
    out.append("<h3>Fab OS %s</h3>" % html.escape(r.pkgver))
    for p in r.intro:
        out.append('<p class="release-intro">%s</p>' % inline_html(p))
    if r.bullets:
        out.append("<ul>")
        out.extend("<li>%s</li>" % inline_html(b) for b in r.bullets)
        out.append("</ul>")
    for p in r.after:
        out.append("<p>%s</p>" % inline_html(p))
    if r.limits:
        out.append('<p class="release-limits"><b>Known limits:</b> %s</p>' % inline_html(r.limits))
    get = r.get_line("html", "../")
    if get:
        out.append('<p class="release-get"><b>How to get it:</b> %s</p>' % get)
    if r.kind != "withheld":
        out.append('<p class="release-links"><a href="%s" target="_blank" rel="noreferrer">Release notes on GitHub</a></p>'
                   % r.github_url)
    out.append("</article>")
    return "\n".join(out)


def render_page(releases, index_html):
    style = slice_between(index_html, "<style>", "</style>", "the stylesheet")
    header = relink(slice_between(index_html, '<header class="site-header">', "</header>", "the site header"))
    footer = relink(slice_between(index_html, '<footer class="footer">', "</footer>", "the site footer"))
    fonts = "\n".join(ln for ln in index_html.split("\n") if "fonts.g" in ln and ln.startswith("<link"))
    shown = [r for r in releases if not r.draft]
    articles = "\n".join(render_release_html(r).replace('<article class="release" ', '<article class="release" data-page="%d" ' % (i // PAGE_SIZE + 1), 1)
                         for i, r in enumerate(shown))
    pages = max(1, (len(shown) + PAGE_SIZE - 1) // PAGE_SIZE)
    description = ("What changed in every Fab OS release, newest first: the images you install and the over-the-air "
                   "updates installed systems receive on their own.")
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#f8d9e8">
<title>Fab OS changelog — by Patience AI</title>
<meta name="description" content="%(description)s">
<link rel="canonical" href="%(site)s/changelog/">
<meta name="robots" content="index, follow, max-image-preview:large">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Fab OS">
<meta property="og:title" content="Fab OS changelog">
<meta property="og:description" content="%(description)s">
<meta property="og:url" content="%(site)s/changelog/">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="Fab OS changelog">
<meta name="twitter:description" content="%(description)s">
<link rel="alternate" type="application/rss+xml" title="Fab OS updates" href="../updates.xml">
<link rel="icon" href="../fabos.svg">
%(fonts)s
%(style)s
<style>%(page_css)s</style>
</head>
<body>
%(header)s
<main id="top">
<section class="section changelog-hero"><div class="wrap"><div class="section-head"><span class="eyebrow">Changelog</span><h2>What changed in Fab OS.</h2><p>Follow along with the <a href="../updates.xml">RSS feed</a> or the <a href="https://github.com/%(repo)s/releases" target="_blank" rel="noreferrer">releases on GitHub</a>.</p></div></div></section>
<section class="section white-section"><div class="wrap changelog-list" data-pages="%(pages)d" data-page-size="%(page_size)d">
%(articles)s
<nav class="pager" aria-label="Changelog pages" hidden><button type="button" data-go="prev">Newer</button><span class="pager-status"></span><button type="button" data-go="next">Older</button></nav>
</div></section>
</main>
%(footer)s
<script>
(function(){var list=document.querySelector(".changelog-list");if(!list)return;var pages=+list.dataset.pages||1;if(pages<2)return;
var cards=[].slice.call(list.querySelectorAll(".release"));var nav=list.querySelector(".pager");var status=nav.querySelector(".pager-status");var cur=1;
function show(n,keepHash){cur=Math.min(pages,Math.max(1,n));cards.forEach(function(c){if(+c.dataset.page===cur){c.removeAttribute("data-page-hidden")}else{c.setAttribute("data-page-hidden","")}});
var numbers=nav.querySelectorAll("[data-page-number]");numbers.forEach(function(b){if(+b.dataset.pageNumber===cur){b.setAttribute("aria-current","page")}else{b.removeAttribute("aria-current")}});
nav.querySelector("[data-go=prev]").disabled=cur===1;nav.querySelector("[data-go=next]").disabled=cur===pages;status.textContent="Page "+cur+" of "+pages;
if(!keepHash){var top=list.getBoundingClientRect().top+window.pageYOffset-96;if(window.pageYOffset>top)window.scrollTo({top:top,behavior:"smooth"})}}
for(var i=1;i<=pages;i++){var b=document.createElement("button");b.type="button";b.textContent=i;b.dataset.pageNumber=i;b.addEventListener("click",function(){show(+this.dataset.pageNumber)});status.parentNode.insertBefore(b,status)}
nav.querySelector("[data-go=prev]").addEventListener("click",function(){show(cur-1)});nav.querySelector("[data-go=next]").addEventListener("click",function(){show(cur+1)});
function fromHash(){var id=decodeURIComponent(location.hash.slice(1));var card=id&&document.getElementById(id);if(card&&card.dataset.page){show(+card.dataset.page,true);card.scrollIntoView()}else{show(1,true)}}
nav.hidden=false;fromHash();window.addEventListener("hashchange",fromHash)})();
</script>
</body>
</html>
""" % dict(description=html.escape(description, quote=True), site=SITE_URL, fonts=fonts, style=style, page_css=PAGE_CSS,
           header=header, articles=articles, footer=footer, repo=GITHUB_REPO, pages=pages, page_size=PAGE_SIZE)


# ---- the feed -------------------------------------------------------------------------------------------------------
def render_item(r):
    title = "Fab OS %s — %s (%s)" % (r.pkgver, r.kind_words, r.human_date)
    parts = [inline_text(p) for p in r.intro] + [inline_text(b) for b in r.bullets] + [inline_text(p) for p in r.after]
    if r.limits:
        parts.append("Known limits: " + inline_text(r.limits))
    parts.append(r.get_line("text", "/"))
    esc = lambda s: html.escape(s, quote=False)
    return ("  <item><title>%s</title><link>%s/changelog/#%s</link><guid isPermaLink=\"false\">fabos-%s</guid>"
            "<description>%s</description><pubDate>%s</pubDate></item>"
            % (esc(title), SITE_URL, esc(r.anchor), esc(r.pkgver), esc(" ".join(parts)), r.pub_date))


def render_feed(releases, existing_xml):
    cut = existing_xml.find("<item>")
    if cut < 0:
        cut = existing_xml.find("</channel>")
    if cut < 0:
        raise ChangelogError("website/updates.xml: no <channel> to keep")
    head = existing_xml[:cut].rstrip() + "\n"
    items = [render_item(r) for r in releases if r.published]
    return head + "\n".join(items) + "\n</channel></rss>\n"


# ---- release notes ----------------------------------------------------------------------------------------------------
def render_notes(r):
    out = ["## What changed in %s (%s, %s)" % (r.pkgver, r.kind_words, r.human_date), ""]
    for p in r.intro:
        out += [p, ""]
    if r.bullets:
        out += ["- " + b for b in r.bullets] + [""]
    for p in r.after:
        out += [p, ""]
    if r.limits:
        out += ["**Known limits:** " + r.limits, ""]
    get = r.get_line("md", "/")
    if get:
        out += ["**How to get it:** " + get, ""]
    out.append("The full changelog is at %s/changelog/ (feed: %s/updates.xml)." % (SITE_URL, SITE_URL))
    return "\n".join(out) + "\n"


# ---- main -------------------------------------------------------------------------------------------------------------
def find(releases, rev):
    for r in releases:
        if r.rev == rev:
            return r
    raise ChangelogError("no changelog entry for package revision %d" % rev)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--changelog", default=os.path.join(ROOT, "docs", "CHANGELOG.md"))
    ap.add_argument("--site", default=os.path.join(ROOT, "website"), help="where index.html and updates.xml are read")
    ap.add_argument("--out-dir", help="where changelog/index.html and updates.xml are written (default: --site)")
    ap.add_argument("--notes", type=int, metavar="REV", help="print the Markdown notes body for package revision REV")
    ap.add_argument("--info", type=int, metavar="REV", help="print shell facts about the entry for REV")
    ap.add_argument("--list", action="store_true", help="list the entries")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    try:
        releases = parse_changelog(a.changelog)
        brand = read_brand()
        if releases and releases[0].version != brand.get("DISTRO_VERSION", releases[0].version):
            raise ChangelogError("changelog version %s differs from brand DISTRO_VERSION %s"
                                 % (releases[0].version, brand.get("DISTRO_VERSION")))
        if a.list:
            for r in releases:
                print("%-6s %-7s %-9s %-11s %s%s" % (r.pkgver, r.tag, r.kind, r.date, r.status,
                                                    " (%d bullets)" % len(r.bullets)))
            return 0
        if a.info is not None:
            r = find(releases, a.info)
            print("CL_VERSION=%s\nCL_TAG=%s\nCL_KIND=%s\nCL_DATE=%s\nCL_STATUS=%s\nCL_FILE=%s"
                  % (r.pkgver, r.tag, r.kind, r.date, r.status, r.file))
            return 0
        if a.notes is not None:
            r = find(releases, a.notes)
            sys.stdout.write(render_notes(r))
            return 0
        with open(os.path.join(a.site, "index.html"), encoding="utf-8") as fh:
            index_html = fh.read()
        with open(os.path.join(a.site, "updates.xml"), encoding="utf-8") as fh:
            feed_xml = fh.read()
        out = a.out_dir or a.site
        os.makedirs(os.path.join(out, "changelog"), exist_ok=True)
        page_path, feed_path = os.path.join(out, "changelog", "index.html"), os.path.join(out, "updates.xml")
        with open(page_path, "w", encoding="utf-8") as fh:
            fh.write(render_page(releases, index_html))
        with open(feed_path, "w", encoding="utf-8") as fh:
            fh.write(render_feed(releases, feed_xml))
        if not a.quiet:
            shown = [r for r in releases if not r.draft]
            print("wrote %s (%d releases) and %s (%d items)" % (page_path, len(shown), feed_path,
                                                                len([r for r in releases if r.published])))
        return 0
    except ChangelogError as e:
        print("changelog-render: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
