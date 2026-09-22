"""Regression tests for search-tab preservation and honest page completion.

The defects these pin, in the order they were found:

1. Processing a GSE navigated the one browser tab away from the search
   results; page 2's pagination was then attempted on a detail page, the
   failure was swallowed, and a completion manifest covered pages the crawl
   never saw.
2. The first fix's page verification read back the value it had just typed
   into #pageno. A browser input keeps what was typed whether or not the
   page turned, so a failed transition still verified. The fake driver here
   therefore models what a real input does — served value until typed into,
   stale after a reload — and the crawler must prove the results DOM was
   replaced before trusting anything on it.

A fake WebDriver stands in for Selenium; everything else is the real
crawler.
"""

import errno
import json
import logging
import os
import sys
from pathlib import Path

import pytest
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.keys import Keys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import genoar_crawler as gc  # noqa: E402

SEARCH = "search-tab"
TITLE_XPATH = "//p[@class='title']/a[@href]"
COUNT_XPATH = "//h3[@class='result_count left']"
DISPLAY_NAME = 'EntrezSystem2.PEntrez.Gds.Gds_ResultsPanel.Gds_DisplayBar.Display'


class _Element:
    def __init__(self, text=""):
        self.text = text

    def click(self):
        return None

    def get_attribute(self, name):
        return None


class _TitleElement:
    """A result row. Belongs to one rendered DOM; stale after a reload."""

    def __init__(self, driver, generation, gse_id):
        self.driver = driver
        self.generation = generation
        self.gse_id = gse_id
        self.text = f"Title for {gse_id}" if gse_id else "Malformed GEO result"

    def _check_stale(self):
        if self.generation != self.driver.dom_generation:
            raise StaleElementReferenceException("element belongs to the old DOM")

    def is_enabled(self):
        self._check_stale()
        return True

    def find_element(self, by, value):
        self._check_stale()
        if self.gse_id is None:
            raise NoSuchElementException("result has no GSE accession")
        return _Element(text=self.gse_id)

    def get_attribute(self, name):
        self._check_stale()
        return f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={self.gse_id}"


class _PageInput:
    """The #pageno box, the way a browser input actually behaves.

    Its value is the server-rendered current page until something types into
    it; from then on it holds whatever was typed, whether or not the page
    turned. Reading it back therefore proves nothing about navigation — the
    defect the second round of review caught.
    """

    def __init__(self, driver, generation):
        self.driver = driver
        self.generation = generation
        self.typed = None

    def _check_stale(self):
        if self.generation != self.driver.dom_generation:
            raise StaleElementReferenceException("input belongs to the old DOM")

    def clear(self):
        self._check_stale()
        self.typed = None

    def send_keys(self, value):
        self._check_stale()
        if value == Keys.RETURN:
            self.driver.turn_page(int(self.typed))
        else:
            self.typed = (self.typed or "") + str(value)

    def get_attribute(self, name):
        self._check_stale()
        if self.typed is not None:
            return self.typed
        return str(self.driver.search_page)


class _SwitchTo:
    def __init__(self, driver):
        self.driver = driver

    def window(self, handle):
        if handle not in self.driver.handles:
            raise WebDriverException(f"no such window: {handle}")
        self.driver.current_handle = handle

    def new_window(self, kind):
        handle = f"tab-{self.driver.tab_counter}"
        self.driver.tab_counter += 1
        self.driver.handles.append(handle)
        self.driver.current_handle = handle


class FakeDriver:
    """The GEO pages this crawl touches, reduced to what the crawler reads."""

    def __init__(self, pages, total_count=None, page_turns=True,
                 count_readable=True):
        self.pages = pages                # {page_num: [gse_id, ...]}
        self.total_count = (
            total_count if total_count is not None
            else sum(len(v) for v in pages.values())
        )
        self.page_turns = page_turns      # False: GEO ignores the page input
        self.count_readable = count_readable
        self.search_page = 1
        self.dom_generation = 0
        self._pageno = _PageInput(self, 0)
        self.handles = [SEARCH]
        self.current_handle = SEARCH
        self.tab_counter = 0
        self.alive = True
        self.urls = {SEARCH: None}
        self.switch_to = _SwitchTo(self)
        self.session_id = "fake-session"

    # -- what the crawler drives --------------------------------------

    def turn_page(self, page_num):
        if self.page_turns and page_num in self.pages:
            self.search_page = page_num
            self._reload()

    def _reload(self):
        """A server response replaces the DOM: old elements go stale."""
        self.dom_generation += 1
        self._pageno = _PageInput(self, self.dom_generation)

    def get(self, url):
        self.urls[self.current_handle] = url

    @property
    def current_url(self):
        if not self.alive:
            raise WebDriverException("chrome died")
        return self.urls.get(self.current_handle) or "about:blank"

    @property
    def window_handles(self):
        return list(self.handles)

    @property
    def current_window_handle(self):
        return self.current_handle

    def close(self):
        self.handles.remove(self.current_handle)
        self.current_handle = self.handles[0] if self.handles else None

    def quit(self):
        self.alive = False

    def execute_cdp_cmd(self, cmd, params):
        return None

    def maximize_window(self):
        return None

    def set_page_load_timeout(self, seconds):
        return None

    # -- what the crawler reads ---------------------------------------

    def _on_search_results(self):
        return self.current_handle == SEARCH

    def find_elements(self, by, value):
        if value == TITLE_XPATH:
            if not self._on_search_results():
                return []
            return [
                _TitleElement(self, self.dom_generation, g)
                for g in self.pages.get(self.search_page, [])
            ]
        if value == DISPLAY_NAME:
            return [_Element(), _Element()] if self._on_search_results() else []
        return []

    def find_element(self, by, value):
        if value == COUNT_XPATH:
            if self.count_readable and self._on_search_results():
                return _Element(text=f"Items: 1 to 500 of {self.total_count}")
            raise NoSuchElementException("no result count here")
        if value == "pageno":
            if self._on_search_results():
                return self._pageno
            raise NoSuchElementException("no pagination on this page")
        if value.startswith("ps"):
            return _Element()
        raise NoSuchElementException(value)


def stub_chrome_paths(monkeypatch):
    """Stand in for an image's CHROME_*_PATH.

    Neither Chrome nor chromedriver is installed on a dev machine, and the
    crawler now refuses to start when it can find no driver at all. The env
    vars are the documented override, so setting them keeps these tests about
    pagination rather than about this machine.
    """
    monkeypatch.setenv("CHROME_BINARY_PATH", "/stand-in/google-chrome-stable")
    monkeypatch.setenv("CHROME_DRIVER_PATH", "/stand-in/chromedriver")


def make_crawler(tmp_path, monkeypatch, driver):
    stub_chrome_paths(monkeypatch)
    monkeypatch.setattr(gc, "CHROME_PROFILE_ROOT", tmp_path / "chrome-profiles")
    # Only Chrome is replaced. Replacing Service as well is not harmless:
    # selenium imports it lazily, so a stub in its place turns the annotations
    # in selenium's own Chrome class into a TypeError, and this whole file
    # fails inside the crawler images. A Service built from a path that does
    # not exist is fine; nothing here ever launches it.
    monkeypatch.setattr(gc.webdriver, "Chrome", lambda **kwargs: driver)
    monkeypatch.setattr(gc.time, "sleep", lambda seconds: None)
    crawler = gc.SimpleCrawler(output_dir=str(tmp_path / "out"))
    # The staleness wait polls a real clock; with sleep stubbed out a negative
    # test would otherwise spin for the full production timeout.
    crawler.NAVIGATION_TIMEOUT = 0.2
    return crawler


def capture_chrome_arguments(tmp_path, monkeypatch, profile_root=None,
                             out_dir=None, scope=None):
    """Build a crawler against a fake Chrome and return the switches it asked
    Chrome for. The profile root defaults under tmp_path so no test writes to
    the real /data."""
    captured = {}

    def chrome(**kwargs):
        captured["args"] = list(kwargs["options"].arguments)
        return FakeDriver({1: ["GSE1"]})

    stub_chrome_paths(monkeypatch)
    monkeypatch.setattr(
        gc, "CHROME_PROFILE_ROOT",
        Path(profile_root) if profile_root else tmp_path / "chrome-profiles",
    )
    monkeypatch.setattr(gc.webdriver, "Chrome", chrome)
    monkeypatch.setattr(gc.time, "sleep", lambda seconds: None)
    gc.SimpleCrawler(output_dir=str(out_dir or (tmp_path / "out")), scope=scope)
    return captured["args"]


def user_data_dir(args):
    for argument in args:
        if argument.startswith("--user-data-dir="):
            return argument.split("=", 1)[1]
    raise AssertionError(f"no --user-data-dir among {args}")


def visit_detail_then_succeed(crawler):
    """A GSE processor that navigates, the way the real one does."""
    def process(gse_id):
        crawler.driver.get(
            f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={gse_id}"
        )
        return True
    return process


def manifest_path(crawler):
    return crawler.output_dir / gc.COMPLETION_MANIFEST_FILE


class TestSearchTabIsPreserved:
    def test_every_requested_page_is_processed(self, tmp_path, monkeypatch):
        """The headline regression: page 2 exists and must be crawled."""
        driver = FakeDriver({1: ["GSE1"], 2: ["GSE2"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)
        monkeypatch.setattr(
            crawler, "process_gse_direct", visit_detail_then_succeed(crawler)
        )

        crawler.crawl(start_page=1, end_page=2, items_per_page=1)

        assert crawler.downloaded_gse == ["GSE1", "GSE2"]
        manifest = json.loads(manifest_path(crawler).read_text())
        assert manifest["completed_pages"] == {"start": 1, "end": 2}
        assert manifest["processed"] == 2

    def test_gse_tabs_are_closed_and_search_tab_survives(
        self, tmp_path, monkeypatch
    ):
        driver = FakeDriver({1: ["GSE1", "GSE2"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)
        monkeypatch.setattr(
            crawler, "process_gse_direct", visit_detail_then_succeed(crawler)
        )

        crawler.crawl(start_page=1, end_page=1)

        assert driver.window_handles == [SEARCH]
        assert driver.current_handle == SEARCH
        assert driver.urls[SEARCH].startswith("https://www.ncbi.nlm.nih.gov/gds/")

    def test_a_closed_search_tab_stops_the_crawl(self, tmp_path, monkeypatch):
        driver = FakeDriver({1: ["GSE1"], 2: ["GSE2"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)

        def close_search_tab(gse_id):
            driver.handles.remove(SEARCH)
            return True

        monkeypatch.setattr(crawler, "process_gse_direct", close_search_tab)

        with pytest.raises(gc.SearchContextLostError):
            crawler.crawl(start_page=1, end_page=2, items_per_page=1)
        assert not manifest_path(crawler).exists()

    def test_a_dead_chrome_session_is_fatal_after_capture(
        self, tmp_path, monkeypatch
    ):
        driver = FakeDriver({1: ["GSE1"], 2: ["GSE2"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)

        def die_after_processing(gse_id):
            driver.alive = False
            return True

        monkeypatch.setattr(crawler, "process_gse_direct", die_after_processing)

        with pytest.raises(gc.SearchContextLostError):
            crawler.crawl(start_page=1, end_page=2, items_per_page=1)
        assert not manifest_path(crawler).exists()


class TestFailureIsNotSuccess:
    def test_an_unturned_page_fails_the_crawl(self, tmp_path, monkeypatch):
        """Typing a page number is not arriving on that page.

        The input keeps the typed "2" even though GEO never turned the
        page, so reading the value back — the first fix's check — verifies
        nothing. Only a replaced results DOM does.
        """
        driver = FakeDriver({1: ["GSE1"], 2: ["GSE2"]}, page_turns=False)
        crawler = make_crawler(tmp_path, monkeypatch, driver)
        monkeypatch.setattr(
            crawler, "process_gse_direct", visit_detail_then_succeed(crawler)
        )

        with pytest.raises(gc.PageValidationError):
            crawler.crawl(start_page=1, end_page=2, items_per_page=1)
        assert not manifest_path(crawler).exists()

    def test_a_partially_loaded_page_fails_the_crawl(
        self, tmp_path, monkeypatch
    ):
        """GEO says two results exist; the DOM shows one. Not a completed page."""
        driver = FakeDriver({1: ["GSE1"]}, total_count=2)
        crawler = make_crawler(tmp_path, monkeypatch, driver)
        monkeypatch.setattr(
            crawler, "process_gse_direct", visit_detail_then_succeed(crawler)
        )

        with pytest.raises(gc.PageValidationError):
            crawler.crawl(start_page=1, end_page=1)
        assert not manifest_path(crawler).exists()

    def test_a_start_page_beyond_the_results_writes_no_manifest(
        self, tmp_path, monkeypatch
    ):
        """Nothing crawled, so nothing to claim — see TestPageRangeIsCapped
        for the exit code this now carries."""
        driver = FakeDriver({1: ["GSE1"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)

        with pytest.raises(gc.EmptyPageRangeError):
            crawler.crawl(start_page=5, end_page=5)
        assert not manifest_path(crawler).exists()

    def test_an_empty_results_page_fails_the_crawl(self, tmp_path, monkeypatch):
        driver = FakeDriver({1: []}, total_count=1)
        crawler = make_crawler(tmp_path, monkeypatch, driver)

        with pytest.raises(gc.PageValidationError):
            crawler.crawl(start_page=1, end_page=1)
        assert not manifest_path(crawler).exists()

    def test_an_unreadable_result_count_fails_the_crawl(
        self, tmp_path, monkeypatch
    ):
        driver = FakeDriver({1: ["GSE1"]}, count_readable=False)
        crawler = make_crawler(tmp_path, monkeypatch, driver)

        with pytest.raises(gc.PageValidationError):
            crawler.crawl(start_page=1, end_page=1)
        assert not manifest_path(crawler).exists()

    def test_a_result_without_accession_fails_the_page(
        self, tmp_path, monkeypatch
    ):
        driver = FakeDriver({1: ["GSE1", None]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)

        with pytest.raises(gc.PageValidationError):
            crawler.crawl(start_page=1, end_page=1)
        assert not manifest_path(crawler).exists()

    def test_a_malformed_accession_fails_the_page(self, tmp_path, monkeypatch):
        driver = FakeDriver({1: ["NOT-A-GSE"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)

        with pytest.raises(gc.PageValidationError):
            crawler.crawl(start_page=1, end_page=1)
        assert not manifest_path(crawler).exists()

    def test_a_duplicated_accession_on_one_page_fails(
        self, tmp_path, monkeypatch
    ):
        driver = FakeDriver({1: ["GSE1", "GSE1"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)

        with pytest.raises(gc.PageValidationError):
            crawler.crawl(start_page=1, end_page=1)
        assert not manifest_path(crawler).exists()

    def test_a_checkpoint_before_the_requested_start_fails(
        self, tmp_path, monkeypatch
    ):
        """Resuming must not silently crawl pages this request never asked for.

        The checkpoint says it belongs to this request - otherwise it is refused
        one step earlier, for a different reason, and this check would never be
        reached. What is wrong with it is where it resumes."""
        driver = FakeDriver({1: ["GSE1"], 2: ["GSE2"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)
        (crawler.output_dir / gc.CHECKPOINT_FILE).write_text(json.dumps({
            "last_page": 1, "last_gse_index": 0, "downloaded_gse": ["GSE1"],
            "failed_gse": [], "total_pages": 2, "items_per_page": 1,
            "requested_pages": {"start": 2, "end": 2},
            "timestamp": "x",
        }))

        with pytest.raises(ValueError):
            crawler.crawl(start_page=2, end_page=2, items_per_page=1,
                          resume=True)
        assert not manifest_path(crawler).exists()

    def test_a_checkpoint_beyond_the_requested_end_is_not_an_empty_success(
        self, tmp_path, monkeypatch
    ):
        """A resume past the end must not skip the loop entirely — zero pages
        processed, completion manifest written anyway."""
        driver = FakeDriver({1: ["GSE1"], 2: ["GSE2"], 3: ["GSE3"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)
        (crawler.output_dir / gc.CHECKPOINT_FILE).write_text(json.dumps({
            "last_page": 3, "last_gse_index": 0, "downloaded_gse": [],
            "failed_gse": [], "total_pages": 3, "items_per_page": 1,
            "requested_pages": {"start": 1, "end": 2},
            "timestamp": "x",
        }))

        with pytest.raises(ValueError):
            crawler.crawl(start_page=1, end_page=2, items_per_page=1,
                          resume=True)
        assert not manifest_path(crawler).exists()

    def test_an_invalid_range_fails_before_touching_the_network(
        self, tmp_path, monkeypatch
    ):
        driver = FakeDriver({1: ["GSE1"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)

        with pytest.raises(ValueError):
            crawler.crawl(start_page=0, end_page=1)
        assert driver.urls[SEARCH] is None


class TestKbdsHttp2Switch:
    @pytest.fixture
    def chrome_arguments(self, tmp_path, monkeypatch):
        def build():
            return capture_chrome_arguments(tmp_path, monkeypatch)
        return build

    def test_off_by_default(self, chrome_arguments, monkeypatch):
        monkeypatch.delenv("GENOAR_CHROME_DISABLE_HTTP2", raising=False)
        assert "--disable-http2" not in chrome_arguments()

    @pytest.mark.parametrize("value", ["1", "true", "True"])
    def test_opt_in_disables_http2(self, chrome_arguments, monkeypatch, value):
        monkeypatch.setenv("GENOAR_CHROME_DISABLE_HTTP2", value)
        assert "--disable-http2" in chrome_arguments()

    @pytest.mark.parametrize("value", ["", "0", "false", "False"])
    def test_explicit_off_values_change_nothing(
        self, chrome_arguments, monkeypatch, value
    ):
        monkeypatch.setenv("GENOAR_CHROME_DISABLE_HTTP2", value)
        assert "--disable-http2" not in chrome_arguments()

    @pytest.mark.parametrize("value", ["yes-please", "on", "2"])
    def test_a_typo_refuses_to_start_rather_than_silently_off(
        self, chrome_arguments, monkeypatch, value
    ):
        """Whoever sets this is collecting evidence; a typo that silently
        runs with HTTP/2 still on would corrupt the comparison."""
        monkeypatch.setenv("GENOAR_CHROME_DISABLE_HTTP2", value)
        with pytest.raises(ValueError):
            chrome_arguments()

    def test_the_real_docker_runner_forwards_the_switch(self, tmp_path):
        """Execute the runner the way the Snakefile does, against a stub
        docker, and read back what would have reached the container.

        A grep of the script proved nothing: the switch line was present
        while the script itself rejected the `linux` platform the Snakefile
        passes and named Dockerfiles that do not exist — it exited before
        docker ever ran.
        """
        import shutil
        import subprocess

        crawler_dir = Path(gc.__file__).resolve().parent
        workdir = tmp_path / "crawler"
        workdir.mkdir()
        shutil.copy(crawler_dir / "run_docker_parallel.sh", workdir)
        (workdir / "Dockerfile.amd64").write_text("FROM scratch\n")

        docker_log = tmp_path / "docker_calls.log"
        stub = tmp_path / "bin" / "docker"
        stub.parent.mkdir()
        stub.write_text(
            "#!/bin/bash\n"
            f"echo \"$@\" >> {docker_log}\n"
            "case \"$1\" in\n"
            "  images) exit 0 ;;\n"          # no image yet -> build runs
            "  ps) exit 0 ;;\n"
            "  run) echo fake-container-id ;;\n"
            "esac\n"
            "exit 0\n"
        )
        stub.chmod(0o755)

        result = subprocess.run(
            ["bash", "run_docker_parallel.sh", "1", "1", "linux"],
            cwd=workdir,
            env={
                "PATH": f"{stub.parent}:/usr/bin:/bin",
                "GENOAR_CHROME_DISABLE_HTTP2": "1",
                "GENOAR_SKIP_MONITOR": "1",
            },
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        calls = docker_log.read_text()
        assert "-f Dockerfile.amd64" in calls
        assert "-e GENOAR_CHROME_DISABLE_HTTP2=1" in calls
        assert "genoar:amd64" in calls


class TestPageRangeIsCapped:
    """A request can outrun GEO, whose page count changes daily.

    A parallel run slices the range from the count that was true when it
    launched; the last slices can land wholly past the end. Capping keeps the
    slices that still exist honest, and the empty ones must not read as either
    a completed crawl or a malfunction.
    """

    def test_an_end_page_past_the_results_is_capped_with_a_warning(
        self, tmp_path, monkeypatch, caplog
    ):
        driver = FakeDriver({1: ["GSE1"], 2: ["GSE2"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)
        monkeypatch.setattr(
            crawler, "process_gse_direct", visit_detail_then_succeed(crawler)
        )

        with caplog.at_level(logging.WARNING):
            crawler.crawl(start_page=1, end_page=5, items_per_page=1)

        assert crawler.downloaded_gse == ["GSE1", "GSE2"]
        # The manifest keeps both numbers: what was asked for and what exists.
        manifest = json.loads(manifest_path(crawler).read_text())
        assert manifest["requested_pages"] == {"start": 1, "end": 5}
        assert manifest["completed_pages"] == {"start": 1, "end": 2}
        assert "capping" in caplog.text
        assert "5" in caplog.text and "2 pages" in caplog.text

    def test_the_manifest_records_the_corpus_it_capped_against(
        self, tmp_path, monkeypatch
    ):
        """Why the crawl stopped where it did, not just where.

        A slice manifest that ends before its request is either a cap or a
        crawl that stopped early, and the aggregator has to tell them apart to
        decide whether the run may claim its range. The count this crawl read
        off GEO is the only thing here that knows which.
        """
        driver = FakeDriver({1: ["GSE1"], 2: ["GSE2"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)
        monkeypatch.setattr(
            crawler, "process_gse_direct", visit_detail_then_succeed(crawler)
        )

        crawler.crawl(start_page=1, end_page=5, items_per_page=1)

        manifest = json.loads(manifest_path(crawler).read_text())
        assert manifest["completed_pages"] == {"start": 1, "end": 2}
        assert manifest["corpus_pages"] == 2

    def test_the_manifest_records_what_the_crawl_collected(
        self, tmp_path, monkeypatch
    ):
        """Nothing was downloaded here - the GSE processor is stubbed - and the
        manifest says so rather than leaving it to be guessed from a directory
        that also holds every earlier run's results."""
        driver = FakeDriver({1: ["GSE1"], 2: ["GSE2"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)
        monkeypatch.setattr(
            crawler, "process_gse_direct", visit_detail_then_succeed(crawler)
        )
        (crawler.output_dir / "SMTX" / "GSE_from_an_earlier_run.txt.gz").write_text("x")

        crawler.crawl(start_page=1, end_page=2, items_per_page=1)

        manifest = json.loads(manifest_path(crawler).read_text())
        assert manifest["collected_files"] == {
            "SMTX": 0, "SRR": 0, "META": 0, "total": 0
        }

    def test_a_range_wholly_past_the_end_is_not_a_completed_crawl(
        self, tmp_path, monkeypatch
    ):
        """The reported case: workers assigned 51-75 while GEO had 47 pages."""
        driver = FakeDriver({1: ["GSE1"], 2: ["GSE2"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)

        with pytest.raises(gc.EmptyPageRangeError):
            crawler.crawl(start_page=51, end_page=75, items_per_page=1)

        assert crawler.downloaded_gse == []
        assert not manifest_path(crawler).exists()

    def test_an_all_pages_request_from_past_the_end_is_empty_too(
        self, tmp_path, monkeypatch
    ):
        driver = FakeDriver({1: ["GSE1"], 2: ["GSE2"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)

        with pytest.raises(gc.EmptyPageRangeError):
            crawler.crawl(start_page=51, end_page=0, items_per_page=1)
        assert not manifest_path(crawler).exists()

    def test_capping_is_not_an_excuse_to_skip_a_page(self, tmp_path, monkeypatch):
        """Capping trims the request, never the work inside it."""
        driver = FakeDriver({1: ["GSE1"], 2: ["GSE2"], 3: ["GSE3"]})
        crawler = make_crawler(tmp_path, monkeypatch, driver)
        monkeypatch.setattr(
            crawler, "process_gse_direct", visit_detail_then_succeed(crawler)
        )

        crawler.crawl(start_page=2, end_page=99, items_per_page=1)

        assert crawler.downloaded_gse == ["GSE2", "GSE3"]
        manifest = json.loads(manifest_path(crawler).read_text())
        assert manifest["completed_pages"] == {"start": 2, "end": 3}


class TestChromeExecutableResolution:
    """Three images, three layouts, and one hardcoded default that matched one
    of them. Resolution order: env var, PATH, then the known install paths."""

    def _resolve_driver(self, candidates=gc.CHROME_DRIVER_CANDIDATES):
        return gc.resolve_executable(
            "CHROME_DRIVER_PATH", gc.CHROME_DRIVER_NAMES, candidates
        )

    def test_the_env_var_outranks_everything(self, monkeypatch):
        """Existing deployments set it; nothing may second-guess them."""
        monkeypatch.setenv("CHROME_DRIVER_PATH", "/opt/site/chromedriver")
        monkeypatch.setattr(gc.shutil, "which", lambda name: "/usr/bin/chromedriver")
        assert self._resolve_driver() == "/opt/site/chromedriver"

    def test_an_empty_env_var_does_not_count_as_a_choice(self, monkeypatch):
        monkeypatch.setenv("CHROME_DRIVER_PATH", "   ")
        monkeypatch.setattr(gc.shutil, "which", lambda name: "/usr/bin/chromedriver")
        assert self._resolve_driver() == "/usr/bin/chromedriver"

    def test_path_is_searched_before_the_known_locations(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.delenv("CHROME_DRIVER_PATH", raising=False)
        on_path = tmp_path / "from-path" / "chromedriver"
        on_path.parent.mkdir()
        on_path.touch()
        installed = tmp_path / "usr-bin" / "chromedriver"
        installed.parent.mkdir()
        installed.touch()
        monkeypatch.setattr(gc.shutil, "which", lambda name: str(on_path))

        assert self._resolve_driver((str(installed),)) == str(on_path)

    def test_known_locations_are_tried_in_order(self, monkeypatch, tmp_path):
        """/usr/bin (both crawler images) before /usr/local/bin (the root
        image's old layout), so a machine holding both is unambiguous."""
        monkeypatch.delenv("CHROME_DRIVER_PATH", raising=False)
        monkeypatch.setattr(gc.shutil, "which", lambda name: None)
        usr_bin = tmp_path / "usr-bin-chromedriver"
        usr_local = tmp_path / "usr-local-bin-chromedriver"
        usr_bin.touch()
        usr_local.touch()

        assert self._resolve_driver((str(usr_bin), str(usr_local))) == str(usr_bin)
        usr_bin.unlink()
        # Either image layout resolves, not just the first candidate.
        assert self._resolve_driver((str(usr_bin), str(usr_local))) == str(usr_local)

    def test_nothing_anywhere_resolves_to_nothing(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CHROME_DRIVER_PATH", raising=False)
        monkeypatch.setattr(gc.shutil, "which", lambda name: None)
        assert self._resolve_driver((str(tmp_path / "absent"),)) is None

    def test_both_image_browsers_are_recognised(self, monkeypatch, tmp_path):
        """google-chrome-stable (amd64 images) and chromium (arm64)."""
        monkeypatch.delenv("CHROME_BINARY_PATH", raising=False)
        monkeypatch.setattr(gc.shutil, "which", lambda name: None)
        for browser in ("google-chrome-stable", "chromium"):
            installed = tmp_path / browser
            installed.touch()
            assert gc.resolve_executable(
                "CHROME_BINARY_PATH", gc.CHROME_BINARY_NAMES,
                (str(tmp_path / "absent"), str(installed)),
            ) == str(installed)
            installed.unlink()

    def test_a_missing_driver_says_what_it_searched(self, tmp_path, monkeypatch):
        """Instead of Selenium's bare NoSuchDriverException, which is what an
        external run got from the root image."""
        monkeypatch.delenv("CHROME_DRIVER_PATH", raising=False)
        monkeypatch.delenv("CHROME_BINARY_PATH", raising=False)
        monkeypatch.setattr(gc.shutil, "which", lambda name: None)
        monkeypatch.setattr(gc, "CHROME_DRIVER_CANDIDATES",
                            (str(tmp_path / "no-chromedriver"),))
        monkeypatch.setattr(gc, "CHROME_BINARY_CANDIDATES",
                            (str(tmp_path / "no-chrome"),))
        monkeypatch.setattr(gc, "CHROME_PROFILE_ROOT", tmp_path / "chrome-profiles")
        monkeypatch.setattr(
            gc.webdriver, "Chrome",
            lambda **kwargs: pytest.fail("Chrome must not be launched"),
        )

        with pytest.raises(gc.ChromeSetupError) as excinfo:
            gc.SimpleCrawler(output_dir=str(tmp_path / "out"))

        message = str(excinfo.value)
        assert "CHROME_DRIVER_PATH" in message
        assert "chromedriver" in message
        assert str(tmp_path / "no-chromedriver") in message

    def test_a_missing_browser_alone_still_starts(self, tmp_path, monkeypatch):
        """Selenium can find a browser this list does not name; only the
        driver is fatal."""
        monkeypatch.delenv("CHROME_BINARY_PATH", raising=False)
        monkeypatch.setenv("CHROME_DRIVER_PATH", "/stand-in/chromedriver")
        monkeypatch.setattr(gc.shutil, "which", lambda name: None)
        monkeypatch.setattr(gc, "CHROME_BINARY_CANDIDATES",
                            (str(tmp_path / "no-chrome"),))
        monkeypatch.setattr(gc, "CHROME_PROFILE_ROOT", tmp_path / "chrome-profiles")
        monkeypatch.setattr(gc.webdriver, "Chrome",
                            lambda **kwargs: FakeDriver({1: ["GSE1"]}))
        monkeypatch.setattr(gc.time, "sleep", lambda seconds: None)

        crawler = gc.SimpleCrawler(output_dir=str(tmp_path / "out"))
        assert crawler.driver is not None


class TestParallelWorkersDoNotCollide:
    def test_chrome_picks_its_own_debugging_port(self, tmp_path, monkeypatch):
        """A fixed 9222 was a collision for every worker after the first."""
        args = capture_chrome_arguments(tmp_path, monkeypatch)
        assert "--remote-debugging-port=0" in args
        assert not any(a.startswith("--remote-debugging-port=9") for a in args)

    def test_an_unassigned_crawler_still_gets_a_profile_of_its_own(
        self, tmp_path, monkeypatch
    ):
        """A human running the crawler directly, with no orchestrator."""
        root = tmp_path / "chrome-root"
        args = capture_chrome_arguments(tmp_path, monkeypatch, profile_root=root)

        profile = Path(user_data_dir(args))
        assert profile.parent == root
        assert profile.name.startswith(f"worker-{os.getpid()}-")
        assert profile.is_dir()

    def test_two_workers_of_a_run_do_not_share_one_profile(
        self, tmp_path, monkeypatch
    ):
        """The defect: every worker of a run wrote the same /data/.chrome.

        Identity comes from the orchestrator, so this holds even when both
        processes report the same pid - which is exactly what happens in
        Docker, where each container's crawler is PID 1.
        """
        out = tmp_path / "out"
        monkeypatch.setattr(gc.os, "getpid", lambda: 1)

        dirs = []
        for worker_id in ("1", "2"):
            scope = gc.resolve_worker_scope(out, run_id="run-a", worker_id=worker_id)
            dirs.append(
                user_data_dir(
                    capture_chrome_arguments(tmp_path, monkeypatch, out_dir=out,
                                             scope=scope)
                )
            )

        assert dirs[0] != dirs[1]
        assert dirs == [
            str(out / "runs" / "run-a" / "workers" / "worker-1" / "chrome-profile"),
            str(out / "runs" / "run-a" / "workers" / "worker-2" / "chrome-profile"),
        ]

    def test_two_unassigned_workers_at_pid_1_still_differ(
        self, tmp_path, monkeypatch
    ):
        """The fallback identity has to survive a shared PID namespace too.

        Two containers with no assigned worker id are both PID 1; keying the
        profile on the pid alone put them in one directory, and every Chrome
        after the first died on that profile's lock.
        """
        root = tmp_path / "chrome-root"
        monkeypatch.setattr(gc.os, "getpid", lambda: 1)

        first = user_data_dir(
            capture_chrome_arguments(tmp_path, monkeypatch, profile_root=root)
        )
        second = user_data_dir(
            capture_chrome_arguments(tmp_path, monkeypatch, profile_root=root)
        )

        assert first != second


class TestChromeProfileFallback:
    @pytest.mark.parametrize("err", [errno.EACCES, errno.EPERM, errno.EROFS,
                                     errno.ENOENT])
    def test_unusable_profile_locations_fall_back(self, err):
        assert gc._profile_dir_unusable(OSError(err, "nope")) is True

    @pytest.mark.parametrize("err", [errno.ENOSPC, errno.EIO])
    def test_disk_and_io_errors_are_not_swallowed(self, err):
        assert gc._profile_dir_unusable(OSError(err, "bad disk")) is False

    def _refuse_profile_root(self, monkeypatch, root, err):
        """Make mkdir under `root` fail, leaving every other path alone."""
        real_mkdir = gc.Path.mkdir

        def mkdir(self, *args, **kwargs):
            if str(self).startswith(str(root)):
                raise OSError(err, os.strerror(err))
            return real_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(gc.Path, "mkdir", mkdir)

    def test_a_read_only_data_still_falls_back_to_tmp(self, tmp_path, monkeypatch):
        """The 2026-08-10 behaviour, kept: per-worker isolation is added to it,
        not instead of it. The fallback carries the same identity, so two
        workers that both fall back still land in different directories."""
        root = tmp_path / "chrome-root"
        self._refuse_profile_root(monkeypatch, root, errno.EROFS)

        args = capture_chrome_arguments(tmp_path, monkeypatch, profile_root=root)

        profile = user_data_dir(args)
        assert profile.startswith(f"/tmp/chrome-{os.getpid()}-")
        assert "--crash-dumps-dir=/tmp" in args

    def test_the_tmp_fallback_is_per_worker_too(self, tmp_path, monkeypatch):
        """Two assigned workers whose /data is read-only must not collide in
        /tmp either — that would only move the profile-lock crash."""
        root = tmp_path / "chrome-root"
        out = tmp_path / "out"
        self._refuse_profile_root(monkeypatch, root, errno.EROFS)
        monkeypatch.setattr(gc.os, "getpid", lambda: 1)

        profiles = []
        for worker_id in ("1", "2"):
            scope = gc.resolve_worker_scope(out, run_id="run-a", worker_id=worker_id)
            self._refuse_profile_root(monkeypatch, scope.profile_dir, errno.EROFS)
            profiles.append(
                user_data_dir(
                    capture_chrome_arguments(tmp_path, monkeypatch,
                                             profile_root=root, out_dir=out,
                                             scope=scope)
                )
            )

        assert profiles == ["/tmp/chrome-run-a-1", "/tmp/chrome-run-a-2"]

    def test_a_full_disk_is_not_hidden_by_the_fallback(self, tmp_path, monkeypatch):
        root = tmp_path / "chrome-root"
        self._refuse_profile_root(monkeypatch, root, errno.ENOSPC)

        with pytest.raises(OSError):
            capture_chrome_arguments(tmp_path, monkeypatch, profile_root=root)
