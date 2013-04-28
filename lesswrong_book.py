#! /usr/bin/python
## Hey, Python: encoding: utf-8
#
# Copyright (c) 2012-2013 Dato Simó (dato@net.com.org.es)
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation, version 2 [see COPYING.GPLv2].
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
# Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""Generate a PDF document out of Less Wrong sequences (using PrinceXML).

Software requirements:

  - Python            (tested only with 2.7)
  - PrinceXML         (http://princexml.com)
  - BeautifulSoup v4  (easy_install BeautifulSoup4)
  - lxml              (easy_install lxml)

Optional libraries:

  - smartypants       (for “smart quote” support; easy_install smartypants)

See ParseArgs() or --help for options.
"""

# TODO [important]: get the PDF output reviewed / get feedback on any glaring
# mistakes or omissions.

# TODO [important]: download images.

# TODO: get rid of multiple <a id="more"> elements (they produce innocuous
# warnings from PrinceXML).

# TODO: the following articles appear in more than one sequence; if that's
# correct, their ids need to be different (at the moment Prince complains); if
# it's not correct or desired, apply the suitable fix.
#
#  /lw/lt/the_robbers_cave_experiment:
#  /lw/m2/the_litany_against_gurus:
#    #1: Politics is the Mind-Killer
#    #2: Death Spirals and the Cult Attractor
#
#  /lw/m9/aschs_conformity_experiment:
#    #1: Death Spirals and the Cult Attractor
#    #2: Seeing with Fresh Eyes
#
#  /lw/if/your_strength_as_a_rationalist:
#  /lw/ih/absence_of_evidence_is_evidence_of_absence:
#  /lw/il/hindsight_bias:
#  /lw/im/hindsight_devalues_science:
#  /lw/iw/positive_bias_look_into_the_dark:
#    #1: Mysterious Answers to Mysterious Questions
#    #2: Noticing Confusion
#
#  /lw/jr/how_to_convince_me_that_2_2_3:
#    #1: Map and Territory
#    #2: Overly Convenient Excuses
#
#  /lw/s3/the_genetic_fallacy:
#    #1: Seeing with Fresh Eyes
#    #2: The Methaetics Sequence

# TODO [maybe]: add a Table of Contents.

# TODO [maybe]: differentiate somehow between "important" vs. "skippable"
# articles (bold and italics in the sequence pages in the wiki, respectively).

# TODO [maybe]: indicate sequence prerequisites somewhere?

# TODO: handle the sequence for A Human's Guide to Words somehow. It's the last
# article; perhaps put it _before_ the first article proper, if I can manage
# links to work ok. (N.B.: Other sequences like The Fun Theory and The Craft and
# the Community also have similar guides.)

# TODO [maybe]: add missing sequences (Quantum Physics, Fun Theory, The Craft
# and The Community, Advance Epistemology 101).

# TODO [maybe]: add most referenced articles (not already part of the sequences)
# in an appendix. (See 'referenced.txt'.)

import argparse
import atexit
import calendar
import errno
import HTMLParser
import httplib
import json
import logging
import re
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib
import urllib2
import urlparse

from xml.etree import ElementTree as ET
from xml.sax import saxutils

import bs4  # Required; easy_install BeautifulSoup4

try:
  from smartypants import smartyPants  # Optional; easy_install smartypants
except ImportError:
  smartyPants = lambda text, attr="1": text

try:
  import lxml  # Strongly advised; easy_install lxml
except ImportError:
  print >>sys.stderr, (
      "WARNING: you don't have lxml installed, which is quasi-required. If it's"
      "\n"
      "impossible for you to install it, manually edit the script to remove the"
      "\n"
      "sys.exit(2) statement in the source. Read the associated caveats.")
  # CAVEATS: HTMLParser is mostly untested. During early development, I noticed
  # some problems with HTMLParser which made me switched to lxml. One of this
  # problem (handling of unclosed <br> tags) resulted in missing lines in the
  # resulting PDF. I try to workaround that particular issue in the code below
  # if HTMLParser is used, but there may be others. USE AT YOUR OWN RISK.
  sys.exit(2)
  HTML_PARSER = "html.parser"
else:
  HTML_PARSER = "lxml"


class Error(Exception):
  "Base exception for this module."


class CssClass(object):
  TITLE = "title"
  ARTICLE = "article"
  SEQUENCE = "sequence"
  SUBSEQUENCE = "subsequence"
  FOOTNOTE = "footnote"
  A_INTERNAL = "internal"
  A_EXTERNAL = "external"
  DESCRIPTION = "description"
  WEB_NAVIGATION = "web-navigation"
  YUDKOWSKY_SHARE = "share"
  SPOILER = "spoiler"


class LessWrongBook(object):

  def Run(self):
    self.args = self.ParseArgs()
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s: %(message)s", stream=sys.stdout)

    self.ParseConfigs()
    self.DownloadHtml()

    if self.args.download_only:
      logging.info("all files downloaded, exiting because of --download_only")
      return

    # HTML skeleton.
    with open(self.args.html_skel) as f:
      doc = bs4.BeautifulSoup(f.read())

    head = doc.html.head
    body = doc.html.body

    # CSS files.
    css_kwargs = {"rel": "stylesheet",
                  "type": "text/css"}

    for style_kwargs in [dict(href=self.args.css_html),
                         dict(href=self.args.css_pdf, media="print")]:
      style_kwargs.update(css_kwargs)
      head.append(doc.new_tag("link", **style_kwargs))

    # The sequences.
    for seq in self.seqs:
      body.append(self.SequenceToHtml(seq))

    # HTML out.
    with tempfile.NamedTemporaryFile(dir=".", prefix="lesswrong-seq_",
                                     suffix=".html", delete=False) as tmp:
      atexit.register(os.unlink, tmp.name)
      tmp.write(doc.encode("UTF-8"))

    if self.args.save_html:
      html_file = self.args.save_html
      shutil.copy(tmp.name, html_file)
    else:
      html_file = os.path.relpath(tmp.name)  # Make Prince warnings less verbose
                                             # by not including the full path.

    # PDF out.
    subprocess.call([self.args.prince, "--javascript",
                     html_file, self.args.output])

  @staticmethod
  def ParseArgs():
    parser = argparse.ArgumentParser(
        usage="%(prog)s [OPTIONS]",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Generate a PDF document out of Less Wrong sequences.

All options have default values. If you run the program without options, it
will download all the sequences from lesswrong.com, and will output a PDF file
in 'lesswrong-seq.pdf'.

The sequences to be included are listed in the 'sequences.json' file. You may
edit that file in the current directory, or specify an alternate one with:

  --sequence-file PATH

And an option exists to use an alternative HTML skeleton file instead of
'lesswrong-seq_skel.html':

  --html-skel PATH

Some options to redirect the output:

  --output PATH: where to create the PDF file.
  --save-html PATH: keep the intermediate HTML in the specified path.

Finally, you can alter the appearance of the resulting PDF (and HTML) by
editing 'lw-pdf-screen.css' and 'lw-html.css' in the current directory, or
specigying alternate CSS files with --css-pdf and --css-html. The default
'lw-pdf-screen.css' embeds real links to navigate the PDF, which is useful
for reading the PDF in a tablet. There is also 'lw-pdf-paper.css', which uses
footnotes as a navigation mechanism.

Please see below for the full listing of options.""")

    # TODO: formatter_class=argparse.ArgumentDefaultsHelpFormatter would be nice
    # to avoid specifying all the default values in 'help' by hand; but it can't
    # be used in combination with RawDescriptionHelpFormatter above.

    parser.add_argument(
        "-o", "--output", metavar="PATH", default='lesswrong-seq.pdf',
        help="where to store the PDF file (default: 'lesswrong-seq.pdf')")

    parser.add_argument(
        "--save-html", metavar="PATH", default=None,
        help="keep the intermediate HTML in the specified location")

    parser.add_argument(
        "--html-skel", metavar="PATH", default="lesswrong-seq_skel.html",
        help="HTML skeleton to use (default: 'lesswrong-seq_skel.html')")

    parser.add_argument(
        "--sequence-file", metavar="PATH", default="sequences.json",
        help="path of the 'sequences.json' file (default: 'sequences.json')")

    parser.add_argument(
        "-s", "--css-pdf", metavar="PATH", default="lw-pdf-screen.css",
        help=("path to the CSS file to use in the PDF (by PrinceXML) "
              "(default: 'lw-pdf-screen.css')"))

    parser.add_argument(
        "--css-html", metavar="PATH", default="lw-html.css",
        help=("path to the default CSS of the generated HTML file "
              "(default: 'lw-html.css', currently empty)"))

    parser.add_argument(
        "--prince", metavar="PATH", default="prince",
        help="path to the PrinceXML executable (default: 'prince')")

    parser.add_argument(
        "--download-only", action="store_true", default=False,
        help="download articles into the HTML cache directory, do nothing more")

    parser.add_argument(
        "--cachedir", metavar="DIR", default="html_cache",
        help=("directory where to store a cache of the downloaded HTML files "
              "(default: 'html_cache')"))

    parser.add_argument(
        "--check-last-modified", action="store_true",
        help="check Last-Modified header when using items from the HTML cache")

    # Some links to Overcoming Bias are permanent redirects to articles in Less
    # Wrong; we want to use the Less Wrong ones because that allows to use them
    # as internal cross-references, rather than external links.
    parser.add_argument(
        "--urlmap", metavar="PATH", default="urlmap.json",
        help="permanent redirects for URLs found in the articles")

    parser.add_argument(
        "--workarounds", metavar="PATH", default="workarounds.json",
        help="workarounds to apply to some articles to be able to parse them")

    return parser.parse_args()

  def ParseConfigs(self):
    with open(self.args.workarounds) as wf:
      self.fixes = json.load(wf)

    with open(self.args.urlmap) as uf:
      self.urlmap = json.load(uf)

    with open(self.args.sequence_file) as sf:
      self.seqs = json.load(sf)
      self.url_ids = {}

    def _DoSequenceIds(seq_obj):
      for url in seq_obj["articles"]:
        noproto_url = re.sub(r"^https?://|/$", "", url, re.IGNORECASE)
        self.url_ids[url] = re.sub(r"[./]", "-", noproto_url)

    for seq_obj in self.seqs:
      if "subsequences" not in seq_obj:
        _DoSequenceIds(seq_obj)
      elif "articles" in seq_obj:
        raise Error(
            "sequences with both articles and sub-sequences are not supported")
      else:
        for subseq_obj in seq_obj["subsequences"]:
          _DoSequenceIds(subseq_obj)

  def _GetFix(self, url, type):
    for fix in self.fixes.get(url, []):
      if fix.get("type") == type:
        return fix

  def DownloadHtml(self):
    html_cache = self.args.cachedir

    def _DoSequence(seq_obj):
      seq_dir = os.path.join(self.args.cachedir, seq_obj["title"])
      _MkdirP(seq_dir)
      self._DownloadSequence(seq_obj, seq_dir)

    for seq_obj in self.seqs:
      if "articles" in seq_obj:
        _DoSequence(seq_obj)
      elif "subsequences" in seq_obj:
        for subseq_obj in seq_obj["subsequences"]:
          _DoSequence(subseq_obj)

  def _DownloadSequence(self, seq_obj, directory):
    for url in seq_obj["articles"]:
      safe_url = urllib.quote(url, safe="")  # FIXME: Refactor out, this is
                                             # repeated in _IterSeqFilesf().
      path = os.path.join(directory, safe_url)
      self._DownloadUrl(url, path)

  def _DownloadUrl(self, url, path):
    if self._GetFix(url, "no-xml-download") is None:
      # For LessWrong articles under /lw, we retrieve their XML version, because
      # later on it's easier to extract the contents from that version.
      url = os.path.join(url, ".xml")

    try:
      stat_info = os.stat(path)
    except OSError, e:
      if e.errno != errno.ENOENT:
        raise
    else:
      if not self.args.check_last_modified:
        return  # File exists but we needn't check for Last-Modified.
      else:
        mtime = stat_info.st_mtime
        last_modified = _GetLastModifiedStamp(url)

        if (last_modified is not None
            and mtime >= last_modified):
          return
        else:
          logging.info("will re-download %s, it was modified", url)

    data = urllib2.urlopen(url).read()
    with open(path, "w") as f:
      f.write(data)

  def _IterSeqFiles(self, seq_obj):
    seq_dir = os.path.join(self.args.cachedir, seq_obj["title"])

    for url in seq_obj["articles"]:
      if self._GetFix(url, "skip") is not None:
        logging.warn("Skipping %s", url)
      else:
        logging.debug("Processing %s", url)
        safe_url = urllib.quote(url, safe="")
        contents = open(os.path.join(seq_dir, safe_url)).read()
        yield (url, self._ApplyContentFixes(url, contents))

  def _ApplyContentFixes(self, url, contents):
    for fix in self.fixes.get(url, []):
      if fix["type"] == "apply-regex-sub":
        for regex, repl in fix["regex-pairs"]:
          contents = re.sub(regex.encode("utf-8"), repl.encode("utf-8"),
                            contents)
    return contents

  def SequenceToHtml(self, seq_obj):
    seq = self._CreateSeqDiv(seq_obj, kind=CssClass.SEQUENCE)

    if "articles" in seq_obj:
      self._AddArticles(seq, seq_obj)
    elif "subsequences"in seq_obj:
      for ss_obj in seq_obj["subsequences"]:
        subseq = self._CreateSeqDiv(ss_obj, kind=CssClass.SUBSEQUENCE)
        self._AddArticles(subseq, ss_obj)
        seq.append(subseq)

    return seq

  def _CreateSeqDiv(self, seq_obj, kind):
    soup = bs4.BeautifulSoup("")
    seq = soup.new_tag("div")
    seq["class"] = kind

    seq_h = soup.new_tag("h2")
    seq_h.string = smartyPants(seq_obj["title"])
    seq.append(seq_h)

    if "description" in seq_obj:
      # XXX "html.parser" is used here because lxml adds <html></html> around
      # the element. Figure out how to achieve what we need here in a proper
      # manner.
      desc_soup = bs4.BeautifulSoup(smartyPants(seq_obj["description"]),
                                    "html.parser")
      seq_desc = soup.new_tag("div")
      seq_desc["class"] = CssClass.DESCRIPTION
      for elem in desc_soup.children:
        seq_desc.append(elem)
      seq.append(seq_desc)

    return seq

  def _AddArticles(self, seq, seq_obj):
    for url, contents in self._IterSeqFiles(seq_obj):
      parser_fix = self._GetFix(url, "special-parser")

      if parser_fix is not None:
        parser_type = parser_fix["parser"]
      else:
        parser_type = "lw-xml"  # Default.

      if parser_type == "lw-xml":
        item = ET.fromstring(contents).find("./channel/item")
        title = smartyPants(item.find("title").text)
        html_contents = item.find("description").text

      elif parser_type == "lw-html":
        big_soup = bs4.BeautifulSoup(contents, HTML_PARSER)
        title = re.sub(r" - Less Wrong$", "", big_soup.title.string.strip())
        html_contents = str(
            big_soup.select('div[itemprop="description"] > div')[0])

      elif parser_type == "yudkowsky.net":
        big_soup = bs4.BeautifulSoup(contents, HTML_PARSER)
        h1 = big_soup.find("h1")
        div = big_soup.new_tag("div")
        title = h1.string

        for tag in list(h1.next_siblings):  # XXX Why is list() needed here?!
          if (isinstance(tag, bs4.element.Tag)
              and tag.name == "ul"
              and CssClass.YUDKOWSKY_SHARE in tag.get("class", [])):
            break
          else:
            div.append(tag)

        html_contents = str(div)

      else:
        raise Error("unknown special parser %r" % parser_type)

      if HTML_PARSER == "html.parser":
        # There is a problem with HTMLParser's handling of unclosed <br> tags:
        # they will be closed with </br>, but not immediately after the opening
        # tag; the heuristic it uses makes it close the tag after a certain
        # amount of text, which makes Prince ignore it. Fix it the dirty way:
        html_contents = re.sub("<br>", "<br />", html_contents,
                               flags=re.IGNORECASE)

      try:
        soup = bs4.BeautifulSoup(smartyPants(html_contents), HTML_PARSER)
      except HTMLParser.HTMLParseError, e:
        print ">>> Failed source:"
        print html_contents
        raise

      article = soup.find("div")
      article["class"] = CssClass.ARTICLE
      article["id"] = self.url_ids[url] + "_div"

      self._MassageArticleText(article)
      seq.append(article)

      article_h = soup.new_tag("h3")
      article.insert(0, article_h)

      # Put the article title inside a <span> so that <h3> can include the
      # external link, but it's possible to extract only the title itself from
      # the CSS file.
      article_title = soup.new_tag("span")
      article_title["class"] = CssClass.TITLE
      article_title["id"] = self.url_ids[url]
      article_title.string = title
      article_h.append(article_title)

      article_link = soup.new_tag("a")
      article_link["href"] = url
      article_link["class"] = CssClass.A_EXTERNAL
      article_h.append(article_link)

      footnote_link = soup.new_tag("span")
      footnote_link["class"] = CssClass.FOOTNOTE
      footnote_link["href"] = url
      article_h.append(footnote_link)

  def _MassageArticleText(self, article):
    soup = bs4.BeautifulSoup("")  # For creating tags below.

    # Mark with a class the "Part of the Foo sequence" and "Next post:" blurbs,
    # so that the print CSS can avoid displaying them (they are not needed for
    # the PDF version).
    for p in article.select('p[style^="text-align:"]'):
      if re.search(r"^(Part of.*sequence|(Next|Previous) post:|"
                   r"\((end|start) of.*sequence)", p.text, re.IGNORECASE):
        p["class"] = CssClass.WEB_NAVIGATION

    # Fix links to articles in the PDF.
    for a in article.select('a'):
      try:
        href = a["href"]
      except KeyError:
        pass
      else:
        if href.startswith("/lw/"):
          # Use the full URL so as to be able to find it in self.url_ids later
          # on, or have the external link work normally otherwise.
          href = a["href"] = "http://lesswrong.com" + href
        if re.search(r"^https?://lesswrong\.com/lw/[^/]+/[^/]+$", href):
          # Canonicalize LW article URLs by appending a missing trailing slash.
          href += "/"
        if href in self.urlmap:
          # Use the redirection so as to be able to find it in self.url_ids.
          href = a["href"] = self.urlmap[href]
        if href in self.url_ids:
          a["href"] = "#" + self.url_ids[href]
          a["class"] = CssClass.A_INTERNAL
        else:
          a["class"] = CssClass.A_EXTERNAL

        footnote_link = soup.new_tag("span")
        footnote_link["class"] = CssClass.FOOTNOTE
        footnote_link["href"] = a["href"]
        a.insert_after(footnote_link)

    # Text in white are spoilers; mark them with a class for formatting in CSS.
    # I cannot seem to get select('span[style="color: #ffffff"]') to work, so I
    # check for the color with a regular expression.
    white_span_re = re.compile(
        r"^color:\s*(#(?:fff){1,2}|white)", re.IGNORECASE)

    for span in article.select('span[style^="color:"]'):
      if white_span_re.search(span["style"]):
        span["class"] = CssClass.SPOILER
        span["style"] = white_span_re.sub("color: default", span["style"])

def _MkdirP(directory):
  """mkdir -p (create directory & parents without failing if they exist)."""
  try:
    os.makedirs(directory)
  except OSError, e:
    if e.errno != errno.EEXIST:
      raise


def _GetLastModifiedStamp(url):
  """Return a Unix timestamp for the Last-Modified header of a URL."""
  parsed_url = urlparse.urlparse(url)
  conn = httplib.HTTPConnection(parsed_url.netloc)
  conn.request("HEAD", parsed_url.path)  # XXX Assumes no parameters in URL.
  resp = conn.getresponse()
  headers = dict(resp.getheaders())

  if "location" in headers:
    return _GetLastModifiedStamp(headers["location"])
  elif "last-modified" not in headers:
    return None
  else:
    time_str = headers["last-modified"]
    time_tuple = time.strptime(time_str, "%a, %d %b %Y %H:%M:%S %Z")
    return calendar.timegm(time_tuple)  # XXX Asumes UTC.


if __name__ == '__main__':
  LessWrongBook().Run()
