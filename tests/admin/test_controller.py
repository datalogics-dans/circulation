from nose.tools import (
    set_trace,
    eq_,
)
import flask
import json
import feedparser
from werkzeug import ImmutableMultiDict

from ..test_controller import ControllerTest
from api.admin.controller import setup_admin_controllers
from api.problem_details import *
from api.admin.config import (
    Configuration,
    temp_config,
)
from core.model import (
    Admin,
    Complaint,
    create,
    Identifier,
    DataSource,
    CoverageRecord,
    SessionManager
)
from core.testing import (
    AlwaysSuccessfulCoverageProvider,
    NeverSuccessfulCoverageProvider,
)

class AdminControllerTest(ControllerTest):

    def setup(self):
        with temp_config() as config:
            config[Configuration.INCLUDE_ADMIN_INTERFACE] = True

            super(AdminControllerTest, self).setup()

            setup_admin_controllers(self.manager)


class TestWorkController(AdminControllerTest):

    def test_details(self):
        [lp] = self.english_1.license_pools

        lp.suppressed = False
        with self.app.test_request_context("/"):
            response = self.manager.admin_work_controller.details(lp.data_source.name, lp.identifier.identifier)
            eq_(200, response.status_code)
            feed = feedparser.parse(response.get_data())
            [entry] = feed['entries']
            suppress_links = [x['href'] for x in entry['links']
                              if x['rel'] == "http://librarysimplified.org/terms/rel/hide"]
            unsuppress_links = [x['href'] for x in entry['links']
                                if x['rel'] == "http://librarysimplified.org/terms/rel/restore"]
            eq_(0, len(unsuppress_links))
            eq_(1, len(suppress_links))
            assert lp.identifier.identifier in suppress_links[0]

        lp.suppressed = True
        with self.app.test_request_context("/"):
            response = self.manager.admin_work_controller.details(lp.data_source.name, lp.identifier.identifier)
            eq_(200, response.status_code)
            feed = feedparser.parse(response.get_data())
            [entry] = feed['entries']
            suppress_links = [x['href'] for x in entry['links']
                              if x['rel'] == "http://librarysimplified.org/terms/rel/hide"]
            unsuppress_links = [x['href'] for x in entry['links']
                                if x['rel'] == "http://librarysimplified.org/terms/rel/restore"]
            eq_(0, len(suppress_links))
            eq_(1, len(unsuppress_links))
            assert lp.identifier.identifier in unsuppress_links[0]

    def test_edit(self):
        [lp] = self.english_1.license_pools
        with self.app.test_request_context("/"):
            flask.request.form = ImmutableMultiDict([("title", "New title")])
            response = self.manager.admin_work_controller.edit(lp.data_source.name, lp.identifier.identifier)

            eq_(200, response.status_code)
            eq_("New title", self.english_1.title)
            assert "New title" in self.english_1.simple_opds_entry

    def test_suppress(self):
        [lp] = self.english_1.license_pools

        with self.app.test_request_context("/"):
            response = self.manager.admin_work_controller.suppress(lp.data_source.name, lp.identifier.identifier)
            eq_(200, response.status_code)
            eq_(True, lp.suppressed)

    def test_unsuppress(self):
        [lp] = self.english_1.license_pools
        lp.suppressed = True

        with self.app.test_request_context("/"):
            response = self.manager.admin_work_controller.unsuppress(lp.data_source.name, lp.identifier.identifier)
            eq_(200, response.status_code)
            eq_(False, lp.suppressed)

    def test_refresh_metadata(self):
        wrangler = DataSource.lookup(self._db, DataSource.METADATA_WRANGLER)
        success_provider = AlwaysSuccessfulCoverageProvider(
            "Always successful", [Identifier.GUTENBERG_ID], wrangler
        )
        failure_provider = NeverSuccessfulCoverageProvider(
            "Never successful", [Identifier.GUTENBERG_ID], wrangler
        )

        with self.app.test_request_context('/'):
            [lp] = self.english_1.license_pools
            response = self.manager.admin_work_controller.refresh_metadata(
                lp.data_source.name, lp.identifier.identifier, provider=success_provider
            )
            eq_(200, response.status_code)
            # Also, the work has a coverage record now for the wrangler.
            assert CoverageRecord.lookup(lp.identifier, wrangler)

            response = self.manager.admin_work_controller.refresh_metadata(
                lp.data_source.name, lp.identifier.identifier, provider=failure_provider
            )
            eq_(METADATA_REFRESH_FAILURE.status_code, response.status_code)
            eq_(METADATA_REFRESH_FAILURE.detail, response.detail)


class TestSignInController(AdminControllerTest):

    def setup(self):
        super(TestSignInController, self).setup()
        self.admin, ignore = create(
            self._db, Admin, email=u'example@nypl.org', access_token=u'abc123',
            credential=json.dumps({
                u'access_token': u'abc123',
                u'client_id': u'', u'client_secret': u'',
                u'refresh_token': u'', u'token_expiry': u'', u'token_uri': u'',
                u'user_agent': u'', u'invalid': u''
            })
        )

    def test_authenticated_admin_from_request(self):
        with self.app.test_request_context('/admin'):
            flask.session['admin_access_token'] = self.admin.access_token
            response = self.manager.admin_sign_in_controller.authenticated_admin_from_request()
            eq_(self.admin, response)

        # Returns an error if you aren't authenticated.
        with temp_config() as config:
            config[Configuration.GOOGLE_OAUTH_INTEGRATION] = {
                Configuration.GOOGLE_OAUTH_CLIENT_JSON : "/path"
            }
            with self.app.test_request_context('/admin'):
                # You get back a problem detail when you're not authenticated.
                response = self.manager.admin_sign_in_controller.authenticated_admin_from_request()
                eq_(401, response.status_code)
                eq_(INVALID_ADMIN_CREDENTIALS.detail, response.detail)

    def test_authenticated_admin(self):
        # Creates a new admin with fresh details.
        new_admin_details = {
            'email' : u'admin@nypl.org',
            'access_token' : u'tubular',
            'credentials' : u'gnarly',
        }
        admin = self.manager.admin_sign_in_controller.authenticated_admin(new_admin_details)
        eq_('admin@nypl.org', admin.email)
        eq_('tubular', admin.access_token)
        eq_('gnarly', admin.credential)

        # Or overwrites credentials for an existing admin.
        existing_admin_details = {
            'email' : u'example@nypl.org',
            'access_token' : u'bananas',
            'credentials' : u'b-a-n-a-n-a-s',
        }
        admin = self.manager.admin_sign_in_controller.authenticated_admin(existing_admin_details)
        eq_(self.admin.id, admin.id)
        eq_('bananas', self.admin.access_token)
        eq_('b-a-n-a-n-a-s', self.admin.credential)

    def test_admin_signin(self):
        with self.app.test_request_context('/admin/sign_in?redirect=foo'):
            flask.session['admin_access_token'] = self.admin.access_token
            response = self.manager.admin_sign_in_controller.sign_in()
            eq_(302, response.status_code)
            eq_("foo", response.headers["Location"])

    def test_staff_email(self):
        with temp_config() as config:
            config[Configuration.POLICIES][Configuration.ADMIN_AUTH_DOMAIN] = "alibrary.org"
            with self.app.test_request_context('/admin/sign_in'):
                staff_email = self.manager.admin_sign_in_controller.staff_email("working@alibrary.org")
                interloper_email = self.manager.admin_sign_in_controller.staff_email("rando@gmail.com")
                eq_(True, staff_email)
                eq_(False, interloper_email)


class TestFeedController(AdminControllerTest):

    def test_complaints(self):
        type = iter(Complaint.VALID_TYPES)
        type1 = next(type)
        type2 = next(type)
        
        work1 = self._work(
            "fiction work with complaint 1",
            language="eng",
            fiction=True,
            with_open_access_download=True)
        complaint1 = self._complaint(
            work1.license_pools[0],
            type1,
            "complaint source 1",
            "complaint detail 1")
        complaint2 = self._complaint(
            work1.license_pools[0],
            type2,
            "complaint source 2",
            "complaint detail 2")
        work2 = self._work(
            "nonfiction work with complaint",
            language="eng",
            fiction=False,
            with_open_access_download=True)
        complaint3 = self._complaint(
            work2.license_pools[0],
            type1,
            "complaint source 3",
            "complaint detail 3")

        SessionManager.refresh_materialized_views(self._db)
        with self.app.test_request_context("/"):
            response = self.manager.admin_feed_controller.complaints()
            feed = feedparser.parse(response.data)
            entries = feed['entries']

            eq_(len(entries), 2)
