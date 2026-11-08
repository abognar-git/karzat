"""The weekly reader count: who counts as a reader and who does not, on hand-made log lines."""

import unittest

from scripts.traffic_report import HEAVY, analyse

CHROME = "Mozilla/5.0%20(Windows%20NT%2010.0;%20Win64;%20x64)%20AppleWebKit/537.36%20Chrome/130.0"
IPHONE = "Mozilla/5.0%20(iPhone;%20CPU%20iPhone%20OS%2018_7%20like%20Mac%20OS%20X)%20Safari/604.1"


def line(ip, path, ua=CHROME, ref="-", status="200", day="2026-11-09", query="-"):
    return {"date": day, "c-ip": ip, "cs-method": "GET", "cs-uri-stem": path, "sc-status": status,
            "cs(User-Agent)": ua, "cs(Referer)": ref, "cs-uri-query": query, "x-edge-location": "BUD50-P3"}


def visit(ip, path, ua=CHROME, **kw):
    return [line(ip, path, ua, **kw), line(ip, "/assets/karzat.css", ua), line(ip, "/assets/karzat.js", ua)]


class Readers(unittest.TestCase):
    def test_who_counts(self):
        rows = (visit("1.1.1.1", "/") + visit("1.1.1.1", "/ckl43/index.html")              # a reader, two pages
                + visit("2.2.2.2", "/szemely/p041.html", IPHONE)                          # a reader on a phone
                + [line("3.3.3.3", "/szemely/a011.html", ref="https://www.google.com/")]  # assets cached, came from Google
                + [line("4.4.4.4", "/", "Mozilla/5.0%20(compatible;%20PerplexityBot/1.0)")] * 5   # a crawler
                + [line("5.5.5.5", "/")]                                                  # a page and no assets: not a browser
                + visit("6.6.6.6", "/") + [line("6.6.6.6", "/.aws/config/", status="403")]  # a scanner in a browser's name
                + visit("7.7.7.7", "/")                                                   # ourselves
                + [r for i in range(HEAVY + 1) for r in visit("8.8.8.8", f"/ckl34/szavazas/{i}.html")])  # a scraper
        a = analyse(rows, ["7.7.7."])
        self.assertEqual(a["visitors"], 3)
        self.assertEqual(a["views"], 4)
        self.assertEqual(a["mobile_share"], 0.33)
        self.assertEqual(a["referrers"], [("google.com", 1)])
        self.assertEqual(a["bots"], [("PerplexityBot", 5)])
        self.assertEqual(a["disguised"], {"visitors": 1, "views": HEAVY + 1})
        self.assertEqual(a["probes"], 1)

    def test_a_broken_link_is_one_followed_from_our_own_page(self):
        rows = (visit("1.1.1.1", "/") + [line("1.1.1.1", "/ckl43/nincs.html", status="404", ref="https://ogykarzat.hu/")]
                + [line("9.9.9.9", "/wp-login.php", status="404"), line("9.9.9.9", "/regi-cim.html", status="404")])
        self.assertEqual(analyse(rows, [])["missing"], [("/ckl43/nincs.html", 1)])


    def test_the_counter_decides_once_it_is_live(self):
        def ping(ip, page, ua=CHROME, ref=None):
            q = "p=" + page.replace("/", "%252F") + (f"&r={ref}" if ref else "") + "&n=abc"   # as CloudFront logs it
            return line(ip, "/assets/olvaso.txt", ua, query=q)
        rows = (visit("1.1.1.1", "/") + [ping("1.1.1.1", "/"), ping("1.1.1.1", "/ckl43/", ref="www.facebook.com")]
                + visit("2.2.2.2", "/")                                    # downloaded everything, ran nothing: a program
                + [ping("4.4.4.4", "/", "Mozilla/5.0%20(compatible;%20Googlebot/2.1)")]   # a rendering crawler says so
                + [ping("7.7.7.7", "/")])                                  # ourselves
        a = analyse(rows, ["7.7.7."])
        self.assertEqual(a["method"], "számláló")
        self.assertEqual((a["visitors"], a["views"]), (1, 2))
        self.assertEqual(a["top_pages"], [("/", 1), ("/ckl43/", 1)])
        self.assertEqual(a["referrers"], [("facebook.com", 1)])


if __name__ == "__main__":
    unittest.main()
