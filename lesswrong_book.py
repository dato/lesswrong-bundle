#! /usr/bin/python
## Hey, Python: enccoding: utf-8
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

"""Generate a PDF book out of Less Wrong sequences (using PrinceXML).

Software requirements:

  - Python            (tested only with 2.7)
  - PrinceXML         (http://princexml.com)
  - BeautifulSoup v4  (easy_install BeautifulSoup4)
  - lxml              (easy_install lxml)

Optional libraries:

  - smartypants       (for “smart quote” support; easy_install smartypants)

See ParseArgs() or --help for options.
"""

# TODO [top item]: fix breakage with the following articles:
ARTICLE_BLACKLIST = set([
    # This one has some embedded control sequences (^K, ^N) where ff/ffi
    # ligatures (or plain characters) should be, and that makes the XML parser
    # barf. Can be fixed by implementing support for article diffs. (Possible
    # follow-up TODO: contact Yudkowsky about getting rid of those control
    # sequences.)
    "http://lesswrong.com/lw/nu/taboo_your_words/",
    # This one has malformed XML: the HTML body of the article is not properly
    # escaped in the XML version.
    "http://lesswrong.com/lw/lw/reversed_stupidity_is_not_intelligence/",
    # These two are not lesswrong.com articles, but the current code always
    # expects XML versions of the articles. (They just need to be parsed with
    # BeautifulSoup directly).
    "http://yudkowsky.net/rational/the-simple-truth",
    "http://yudkowsky.net/rational/bayes",
])

# TODO [important]: get the PDF output reviewed / get feedback on any glaring
# mistakes or omissions.

# TODO [important]: implement navigation/cross-references support.

# TODO [important]: download images.

# TODO: get rid of multiple <a id="more"> elements (they produce inoccuous
# warnings from PrinceXML).

# TODO: the following articles appear in more than one sequence; if that's
# correct, their ids need to be different (at the moment Prince complains); if
# it's not correct, apply the suitable fix.
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

# TODO [maybe]: differenciate somehow between "important" vs. "skippable"
# articles (bold and italics in the sequence pages in the wiki).

# TODO [maybe]: indicate sequence prerequisites somewhere?

# TODO: handle the sequence for A Human's Guide to Words somehow. It's the last
# article; perhaps put it _before_ the first article proper, if I can manage
# links to work ok. (N.B.: Other sequences like The Fun Theory and The Craft and
# the Community also have similar guides.)

# TODO [maybe]: add missing sequences (Quantum Physics, Fun Theory, The Craft
# and The Community, Advance Epistemology 101).

# TODO [maybe]: add referenced articles not part of the sequences in an
# appendix.

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

HTML_SKELETON = """
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>LessWrong.com Sequences</title>
  <meta name="author" content="Elizier Yudkowsky" />
  <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
  <script language="javascript">
    function pad2(x) {
      return (x < 10) ? ("0" + x) : x;
    }
    function datestamp() {
      var d = new Date();
      return (d.getUTCFullYear()
              + "-" + pad2(d.getUTCMonth() + 1)
              + "-" + pad2(d.getUTCDate()));
    }
    function generateUUID() {  // From http://stackoverflow.com/a/8809472.
      var d = new Date().getTime();
      var uuid = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,
        function(c) {
          var r = (d + Math.random()*16) % 16 | 0;
          d = Math.floor(d/16);
          return (c == 'x' ? r : (r & 0x7 | 0x8)).toString(16);
        });
      return uuid;
    }
  </script>
</head>
<body>
  <div class="cover">
    <h1>LessWrong.com Sequences</h1>
    <div class="author">Elizier Yudkowsky</div>
    <div class="generator">Generated by
      <a href="https://github.com/dato/lesswrong-bundle">lesswrong_book.py</a>
      on <span id="date-generated" />.
      <br />Pseudo-random version string: <span id="uuid" />.
    </div>
  </div>
</body>
</html>
"""

class Error(Exception):
  "Base exception for this module."


class LessWrongBook(object):

  def Run(self):
    self.args = self.ParseArgs()
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s: %(message)s", stream=sys.stdout)

    self.ParseSeqList()
    self.DownloadHtml()

    if self.args.download_only:
      logging.info("all files downloaded, exiting because of --download_only")
      return

    # HTML skeleton.
    doc = bs4.BeautifulSoup(HTML_SKELETON)
    head = doc.html.head
    body = doc.html.body

    # CSS files.
    css_kwargs = {"rel": "stylesheet",
                  "type": "text/css"}

    for style_kwargs in [dict(href=self.args.css_screen),
                         dict(href=self.args.css_print, media="print")]:
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
        description="""Generate a PDF book out of Less Wrong sequences.

All options have default values. If you run the program without options, it
will download all the sequences from lesswrong.com, and will output a PDF file
in 'lesswrong-seq.pdf'.

Interesting options to modify the output:

  --output PATH: where to create the PDF file.
  --save-html PATH: keep the intermediate HTML in the specified path.

The sequences to be included are listed in the 'sequences.json' file. You may
edit that file in the current directory, or specify an alternate one with:

  --sequence-file PATH

Finally, you can alter the appearance of the resulting PDF (and HTML) by
editing 'lesswrong-print.css' and 'lesswrong-screen.css' in the current
directory, or specigying alternate CSS files with --css-print and --css-screen.

Please see below for the full listing of options.""")

    # TODO: formatter_class=argparse.ArgumentDefaultsHelpFormatter would be nice
    # to avoid specifying all the default values in 'help' by hand; but it can't
    # be used in combination with RawDescriptionHelpFormatter above.

    parser.add_argument(
        "-o", "--output", metavar="PATH", default='lesswrong-seq.pdf',
        help="where to store the PDF book (default: 'lesswrong-seq.pdf')")

    parser.add_argument(
        "--save-html", metavar="PATH", default=None,
        help="keep the intermediate HTML in the specified location")

    parser.add_argument(
        "--sequence-file", metavar="PATH", default="sequences.json",
        help="path of the 'sequences.json' file (default: 'sequences.json')")

    parser.add_argument(
        "-s", "--css-print", metavar="PATH", default="lesswrong-print.css",
        help=("path to the CSS file to use in the PDF (by PrinceXML) "
              "(default: 'lesswrong-print.css')"))

    parser.add_argument(
        "--css-screen", metavar="PATH", default="lesswrong-screen.css",
        help=("path to the default CSS of the generated HTML file "
              "(default: 'lesswrong-screen.css', currently empty)"))

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

    return parser.parse_args()

  def ParseSeqList(self):
    with open(self.args.sequence_file) as sf:
      self.seqs = json.load(sf)

    # For LessWrong articles under /lw, we retrieve their XML version, because
    # later on it's easier to extract the contents from that version.
    for seq_obj in self.seqs:
      for l in ([seq_obj.get("articles")] +
                [ss["articles"] for ss in seq_obj.get("subsequences", [])]):
        if l is not None:
          l[:] = [os.path.join(url, ".xml") if self._IsLwRedditUrl(url) else url
                  for url in l
                  if url not in ARTICLE_BLACKLIST]

  @staticmethod
  def _IsLwRedditUrl(url):
    return url.startswith("http://lesswrong.com/lw/")

  def DownloadHtml(self):
    html_cache = self.args.cachedir

    def _DoSequence(seq_obj):
      seq_dir = os.path.join(self.args.cachedir, seq_obj["title"])
      _MkdirP(seq_dir)
      self._DownloadSequence(seq_obj, seq_dir)

    for seq_obj in self.seqs:
      # FIXME: Refactor out, this is repeated in SequenceToHtml().
      if "subsequences" not in seq_obj:
        _DoSequence(seq_obj)
      elif "articles" in seq_obj:
        raise Error(
            "sequences with both articles and sub-sequences are not supported")
      else:
        for subseq_obj in seq_obj["subsequences"]:
          _DoSequence(subseq_obj)

  def _DownloadSequence(self, seq_obj, directory):
    for url in seq_obj["articles"]:
      safe_url = urllib.quote(url, safe="")  # FIXME: Refactor out, this is
                                             # repeated in _IterSeqFilesf().
      path = os.path.join(directory, safe_url)
      self._DownloadUrl(url, path)

  def _DownloadUrl(self, url, path):
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
      logging.debug("Processing %s", url)
      safe_url = urllib.quote(url, safe="")
      yield os.path.join(seq_dir, safe_url)

  def SequenceToHtml(self, seq_obj):
    seq = self._CreateSeqDiv(seq_obj, kind="sequence")

    if "subsequences" not in seq_obj:
      self._AddArticles(seq, seq_obj)
    elif "articles" in seq_obj:
      raise Error(
          "sequences with both articles and sub-sequences are not supported")
    else:
      for ss_obj in seq_obj["subsequences"]:
        subseq = self._CreateSeqDiv(ss_obj, kind="subsequence")
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
      seq_desc["class"] = "description"
      for elem in desc_soup.children:
        seq_desc.append(elem)
      seq.append(seq_desc)

    return seq

  def _AddArticles(self, seq, seq_obj):
    for f in self._IterSeqFiles(seq_obj):
      item = ET.parse(f).getroot().find("./channel/item")
      title = smartyPants(item.find("title").text)
      link = item.find("link").text
      article_id = re.sub(r"^https?://lesswrong.com/", "",
                          item.find("guid").text, re.IGNORECASE)
      html_contents = item.find("description").text

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
      article["class"] = "article"
      article["id"] = article_id

      self._MassageArticleText(article)
      seq.append(article)

      article_h = soup.new_tag("h3")
      article.insert(0, article_h)

      # Put the article title inside a <span> so that <h3> can include the
      # external link, but it's possible to extract only the title itself from
      # the CSS file.
      article_title = soup.new_tag("span")
      article_title["class"] = "title"
      article_title.string = title
      article_h.append(article_title)

      article_link = soup.new_tag("a")
      article_link["href"] = link
      article_link.string = u"↗"
      article_h.append(article_link)

  def _MassageArticleText(self, article):
    # Mark with a class the "Part of the Foo sequence" and "Next post:" blurbs,
    # so that the print CSS can avoid displaying them (they are not needed for
    # the book).
    for p in article.select('p[style="text-align:right"]'):
      if re.search(r"^(Part of.*sequence|(Next|Previous) post:|"
                   r"\((end|start) of.*sequence)", p.text):
        p["class"] = "web-navigation"


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
