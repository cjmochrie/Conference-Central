Part of an Udacity Nanodegree

Conference Central Api extension
By: Cameron Mochrie

Required Libraries:
Google app engine SDK
datetime

Files Developed (beyond Udacity's provided code):
conference.py - main application for running the api.
models.py - Class declarations for various datastore and protorpc messeges.
Task 1 Design Choices.txt - answer to Task 1 question.
Task 3 Query Problems.txt - answers and discusion for Task 3 questions.

Instructions:

This project is a partially completed API for a Conference scheduling application. See below for a list of API
methods I implemented and brief instruction (provided by Udacity) follows:

addSessionToWishlist - Takes a single argument, websafeKey of the session the user would like to add to their wish list.
User must be logged in to use this method.

removeSessionFromWishlist - Takes a single argument, websafeKey of the session the user wants removed from their wishlist.
User must be logged in to use this method.

getSessionWishlist - returns a User's wishlist of Sessions they are thinking of attending.

createSession - Takes two required arguments: 'websafeConferenceKey' of the conference where the session will be held
and 'name' - the name of the session. Other optional arguments are: date, duration, highlights (list), location, startTime,
typeOfSession. A Speaker can be provided in one of two ways: providing a websafeSpeakerKey for a Speaker that is already
'in the system', or providing a new speaker name with optional email. In the second case a new Speaker will be created
and a websafeSpeakerKey returned in a protorpc message along with the session information. A user is required to be
logged in, and he/she must be the creator of the conference where the session will be held.

createSpeaker - User must be logged in. Creates a Speaker, requires a 'name' and optional email argument. Will return a
websafeSpeakerKey.

getConferenceSessions - Takes a single required argument 'websafeConferenceKey' and will return all sessions taking
place at the given conference.

getConferenceSessionsByType - identical to the previous method with the addition of a 'typeOfSession' argument.

getFeaturedSpeaker - returns a message taken from memcache highlighting a recent speaker with multiple upcoming sessions.
This value is refreshed whenever a session is added that brings a speaker's total session count above 1.

getSessionsBySpeaker - takes a websafeSpeakerKey argument and returns all the Sessions that the Speaker is speaking at.

querySessions - a general purpose method for querying Sessions similar to the queryConferences method. Possible fields
are: 'ROOM', 'HIGHLIGHTS', 'SPEAKER_NAME', 'DURATION', 'TYPE_OF_SESSION', 'START_TIME', and 'LOCATION'.

getConferencesBySpeaker - A simple method for identifying conferences that a certain speaker is speaking at.
Requires only a websafeSpeakerKey and returns all the applicable conferences.

Methods that would need to be added for full functionality (self-explanatory):
updateSession
updateSpeaker
removeSession
removeSpeaker


-----

App Engine application for the Udacity training course.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool
