#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'

import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb
from google.net.proto.ProtocolBuffer import ProtocolBufferDecodeError

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import Session
from models import SessionForm
from models import SessionForms
from models import SessionQueryForm
from models import SessionQueryForms

from models import Speaker
from models import SpeakerForm

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

from utils import getUserId

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
MEMCACHE_FEATURED_SPEAKER_KEY = "FEATURED_SPEAKER"
FEATURED_SPEAKER_TPL = ('%s is giving several sessions at an upcoming conference! They are: %s')
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": ["Default", "Topic"],
}

SESS_DEFAULTS = {
    'highlights': ['Default Highlight'],
    'duration': 60,
    'typeOfSession': 'Default Session',
    'location': 'Default Location',
    'speakerName': 'TBD',

}

OPERATORS = {
    'EQ': '=',
    'GT': '>',
    'GTEQ': '>=',
    'LT': '<',
    'LTEQ': '<=',
    'NE': '!='
}

CONFERENCE_FIELDS = {
    'CITY': 'city',
    'TOPIC': 'topics',
    'MONTH': 'month',
    'MAX_ATTENDEES': 'maxAttendees',
}

SESSION_FIELDS = {
    'SPEAKER_NAME': 'speakerName',
    'DURATION': 'duration',
    'TYPE_OF_SESSION': 'typeOfSession',
    'START_TIME': 'startTime',
    'LOCATION': 'location',
    'WEBSAFE_CONFERENCE_KEY' : 'websafeConferenceKey',
}

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)


SESS_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    typeOfSession=messages.StringField(2),
)

SPEAKER_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSpeakerKey=messages.StringField(1),
)

WISH_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeKey=messages.StringField(1)
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
               allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
               scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""


    # - - -- - - Session objects - - - - -- - --

    @endpoints.method(SPEAKER_GET_REQUEST, SessionForms, path='/{websafeSpeakerKey}/sessions',
                      http_method='POST', name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Get all sessions by the given speaker all conferences"""
        speaker = ndb.Key(urlsafe=request.websafeSpeakerKey).get()
        sessions = [sessionKey.get() for sessionKey in speaker.sessionKeys]

        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(SESS_GET_REQUEST, SessionForms, path='conference/{websafeConferenceKey}/{typeOfSession}',
                      http_method='POST', name="getConferenceSessionsByType")
    def getConferenceSessionsByType(self, request):
        """Get all sessions of the given type from the selected conference"""
        sessions = Session.query(ancestor=ndb.Key(urlsafe=request.websafeConferenceKey)).filter(
            Session.typeOfSession == request.typeOfSession)
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(SESS_GET_REQUEST, SessionForms, path='conference/{websafeConferenceKey}/sessions',
                      http_method='POST', name="getConferenceSessions")
    def getConferenceSessions(self, request):
        """Get all sessions from the selected conference"""

        sessions = Session.query(ancestor=ndb.Key(urlsafe=request.websafeConferenceKey)).fetch()

        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(SessionForm, SessionForm, path='session',
                      http_method='POST', name='createSession')
    def createSession(self, request):
        """Create new session."""
        return self._createSessionObject(request)

    def _createSessionObject(self, request):
        """Create a new session object, and if necessary, a new speaker object"""

        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required")

        # get key of the conference where the session will be held
        conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        conf = conf_key.get()

        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # If session creator has provided a speaker key, check that it is valid and retrieve
        # his/her name if possible
        # Otherwise create a new speaker (if a speaker name is provided) and assign a a key otherwise continue
        # building the session without a speaker
        if data['websafeSpeakerKey'] is not None:
            try:
                speaker_key = ndb.Key(urlsafe=request.websafeSpeakerKey)
                speaker = speaker_key.get()
                data['speakerName'] = speaker.name
                data['speakerEmail'] = speaker.mainEmail

            except ProtocolBufferDecodeError:
                raise endpoints.NotFoundException(
                    'No speaker found with websafeSpeakerKey: %s. Create a new speaker or enter the key again'
                    % data['websafeSpeakerKey'])

        else:
            # Create a new speaker object if a name is provided in the SessionForm
            if data['speakerName'] is not None:
                speaker = Speaker(name=data['speakerName'], mainEmail=data['speakerEmail'])
                speaker.put()
                data['websafeSpeakerKey'] = speaker.key.urlsafe()
                speaker.websafeKey = speaker.key.urlsafe()

        # add default values for those missing (both data model & outbound Message)
        for df in SESS_DEFAULTS:
            if data[df] in (None, []):
                data[df] = SESS_DEFAULTS[df]
                setattr(request, df, SESS_DEFAULTS[df])

        # convert Date from string to Date object
        if data['date'] is not None:
            data['date'] = datetime.datetime.strptime(data['date'][:10], "%Y-%m-%d").date()

        # convert startTime from string to Time object
        if data['startTime'] is not None:
            data['startTime'] = datetime.datetime.strptime(data['startTime'][:], "%H:%M").time()

        # websafeKey only used for return messages
        del data['websafeKey']
        # Assign the new session a conference parent
        data['parent'] = conf_key
        # Create the sesion and put to the database
        sess = Session(**data)
        sess_key = sess.put()

        # update speaker (if necessary) so that this session key will be added to his list of session keys
        if speaker is not None:
            speaker.sessionKeys.append(sess_key)
            speaker.put()

            # Check the speaker's sessions to see if he/she already has a session at the same conference
            # If so, set a featured speaker memcache entry to list all of his or her sessions
            parent_keys = [sess_key.parent() for sess_key in speaker.sessionKeys]

            if sess_key.parent() in parent_keys:
                featured_sessions = [sess_key.get() for sess_key in speaker.sessionKeys]
                featured_speaker_string = FEATURED_SPEAKER_TPL % (speaker.name, ', '.join(sess.name for sess in featured_sessions))
                memcache.set(MEMCACHE_FEATURED_SPEAKER_KEY, featured_speaker_string)


        return self._copySessionToForm(sess)

    def _copySessionToForm(self, sess):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(sess, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('date') or field.name.endswith('Time'):
                    setattr(sf, field.name, str(getattr(sess, field.name)))
                else:
                    setattr(sf, field.name, getattr(sess, field.name))

        # set the websafeKey to the Session Form so it can be returned with a websafeKey that the user can use
        setattr(sf, 'websafeKey', sess.key.urlsafe())
        sf.check_initialized()
        return sf

    def _getQuerySession(self, request):
        """Return formatted query from the submitted filters."""
        q = Session.query()
        inequality_filter, filters = self._formatFilters(request.filters, type='Session')

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Session.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Session.name)

        for filtr in filters:
            if filtr["field"] in ["duration"]:
                filtr["value"] = int(filtr["value"])
            elif filtr["field"] in ["startTime"]:
                filtr["value"] = datetime.datetime.strptime("1970-01-01" + ' ' + filtr['value'][:], "%Y-%m-%d %H:%M")

            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)

        return q

    @endpoints.method(SessionQueryForms, SessionForms,
                      path='querySessions',
                      http_method='POST',
                      name='querySessions')
    def querySessions(self, request):
        """Query for sessions."""
        sessions = self._getQuerySession(request)
        q = sessions.fetch()

        # return individual SessionForm object per Session
        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in sessions]
        )

    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

    # ---- Speaker objects -- - - --  - - - - - - - - - - - - - -
    @endpoints.method(SpeakerForm, SpeakerForm, path='createSpeaker',
                      http_method='POST', name='createSpeaker')
    def createSpeaker(self, request):
        """ Public method for creating speakers """

        # Only logged in users can create Speakers
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        """Create new speaker."""
        return self._createSpeakerObject(request)

    # Create a speaker object and add to datastore
    # return key
    def _createSpeakerObject(self, request):
        speaker = Speaker(name=request.name, mainEmail=request.mainEmail)
        speaker.put()
        speaker.websafeKey = speaker.key.urlsafe()
        speaker.put()
        return self._copySpeakerToForm(speaker)

    def _copySpeakerToForm(self, speaker):
        """Copy relevant fields from Session to SessionForm."""
        sf = SpeakerForm()
        for field in sf.all_fields():
            if hasattr(speaker, field.name):
                setattr(sf, field.name, getattr(speaker, field.name))

        sf.check_initialized()
        return sf

    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


    # - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf

    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.datetime.trptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
                              'conferenceInfo': repr(request)},
                      url='/tasks/send_confirmation_email'
                      )
        return request

    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
                      http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)

    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)

    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='getConferencesCreated',
                      http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )

    @endpoints.method(SPEAKER_GET_REQUEST, ConferenceForms,
                      path='conferences/{websafeSpeakerKey}',
                      http_method='POST', name='getConferencesBySpeaker')
    def getConferencesBySpeaker(self, request):
        """Return conferences that feature a certain speaker"""

        # Get Speaker and his/her sessions from websafeSpeakerKey
        speaker = ndb.Key(urlsafe=request.websafeSpeakerKey).get()
        sessions = [sessionKey.get() for sessionKey in speaker.sessionKeys]

        # Get all the unique conference keys and get the conference objects
        conf_keys = []
        for sess in sessions:
            if sess.websafeConferenceKey not in conf_keys:
                conf_keys.append(sess.websafeConferenceKey)

        confs = [ndb.Key(urlsafe=key).get() for key in conf_keys]

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, displayName='') for conf in confs]
        )



    def _getQueryConference(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q

    def _formatFilters(self, filters, type='Conference'):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            # Check the fields are valid - default format is conference query.
            try:
                if type == 'Conference':
                    filtr["field"] = CONFERENCE_FIELDS[filtr["field"]]
                elif type == 'Session':
                    filtr["field"] = SESSION_FIELDS[filtr["field"]]

                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)

    @endpoints.method(ConferenceQueryForms, ConferenceForms,
                      path='queryConferences',
                      http_method='POST',
                      name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQueryConference(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                   conferences]
        )

    # - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf

    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key=p_key,
                displayName=user.nickname(),
                mainEmail=user.email(),
                teeShirtSize=str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile  # return Profile

    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        # if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        # else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)

    @endpoints.method(message_types.VoidMessage, ProfileForm,
                      path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()

    @endpoints.method(ProfileMiniForm, ProfileForm,
                      path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)

        # - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement

    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='conference/announcement/get',
                      http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='session/featuredSpeaker/get',
                      http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return Featured speaker announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_FEATURED_SPEAKER_KEY) or "")

    # - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser()  # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='conferences/attending',
                      http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser()  # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) \
                                      for conf in conferences])

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)

    @ndb.transactional(xg=True)
    def _wishlistAdd(self, request, add=True):
        """Add or remove session from user wishlist."""
        retval = None
        prof = self._getProfileFromUser()  # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeKey
        sess = ndb.Key(urlsafe=wsck).get()
        if not sess:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % wsck)

        # Add
        if add:
            # check if user already added the session otherwise add
            if wsck in prof.sessionKeysWishlist:
                raise ConflictException(
                    "You have already added this session to your wishlist")

            # add the session
            prof.sessionKeysWishlist.append(wsck)
            retval = True

        # remove from wishlist
        else:
            # check if wishlist already contains the session
            if wsck in prof.sessionKeysWishlist:
                # remove the session from the list
                prof.sessionKeysWishlist.remove(wsck)
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        sess.put()
        return BooleanMessage(data=retval)

    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='wishlist/sessions',
                      http_method='GET', name='getSessionWishlist')
    def getSessionWishlist(self, request):
        """Get list of sessions that user has added to their weishlist."""
        # get user Profile
        prof = self._getProfileFromUser()

        # Get the keys from the user profile then the sessions themselves
        sess_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.sessionKeysWishlist]
        sessions = ndb.get_multi(sess_keys)

        # return set of SessionForm objects
        return SessionForms(items=[self._copySessionToForm(sess) for sess in sessions])

    @endpoints.method(WISH_GET_REQUEST, BooleanMessage,
                      path='wishlist/{websafeKey}',
                      http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Add session to user's wishlist."""
        return self._wishlistAdd(request)

    @endpoints.method(WISH_GET_REQUEST, BooleanMessage,
                      path='wishlist/{websafeKey}',
                      http_method='DELETE', name='removeSessionFromWishlist')
    def removeSessionFromWishlist(self, request):
        """Remove a session from a user's wishlist."""
        return self._wishlistAdd(request, add=False)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='filterPlayground',
                      http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Conference.query()
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)
        q = q.filter(Conference.city == "London")
        q = q.filter(Conference.topics == "Medical Innovations")
        q = q.filter(Conference.month == 6)

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )


api = endpoints.api_server([ConferenceApi])  # register API
