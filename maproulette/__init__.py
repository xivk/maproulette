from flask import Flask, session, request, send_from_directory, jsonify, \
    render_template, Response, url_for, redirect
from flask_oauth import OAuth
from simplekv.fs import FilesystemStore
from flaskext.kvsession import KVSessionExtension
from flaskext.coffee import coffee
import requests
from random import choice
from shapely.geometry import asShape, Point
import geojson
from xml.etree import ElementTree as ET
import sys
from flask.ext.mongoengine import MongoEngine

try:
    import settings
except ImportError:
    sys.stderr("""There must be a settings.py file with a secret_key.
    Run bin/make_secret.py
    """)
    sys.exit(2)

# initialize server KV session store
store = FilesystemStore('./sessiondata')

# instantiate flask app
app = Flask(__name__)

# connect flask app to server KV session store
KVSessionExtension(store, app)

# Apps need a secret key
app.secret_key = settings.secret_key

# Coffeescript enable the app
coffee(app)

app.debug = True

# Adding MongoDB configs
app.config["MONGODB_SETTINGS"] = {'DB': "maproulette"}
db = MongoEngine(app)

#initialize osm oauth
# instantite OAuth object
oauth = OAuth()
osm = oauth.remote_app(
    'osm',
    base_url='http://openstreetmap.org/',
    request_token_url = 'http://www.openstreetmap.org/oauth/request_token',
    access_token_url = 'http://www.openstreetmap.org/oauth/access_token',
    authorize_url = 'http://www.openstreetmap.org/oauth/authorize',
    consumer_key = settings.consumer_key,
    consumer_secret = settings.consumer_secret
)

@osm.tokengetter
def get_osm_token(token=None):
    session.regenerate()
    return session.get('osm_token')

# Some helper functions
def parse_user_details(s):
    """Takes a string XML representation of a user's details and
    returns a dictionary of values we care about"""
    root = et.find('./user')
    if not root:
        print 'aaaargh'
        return None
    user = {}
    user['id'] = root.attrib['id']
    user['username'] = root.attrib['display_name']
    try:
        user['lat'] = float(root.find('./home').attrib['lat'])
        user['lon'] = float(root.find('./home').attrib['lon'])
    except AttributeError:
        pass
    user['changesets'] = int(root.find('./changesets').attrib['count'])
    return user

def get_task(challenge, near = None, lock = True):
    """Gets a task and returns the resulting JSON as a string"""
    host = config.get(challenge, 'host')
    port = config.get(challenge, 'port')
    args = ""
    if near:
        args += "near=%(near)s"
    if not lock:
        args += "lock=no"
    if args:
        url = "http://%(host)s:%(port)s/task?%(args)" % {
            'host': host,
            'port': port,
            'args': args}
    else:
        url = "http://%(host)s:%(port)s/task" % {
            'host': host,
            'port': port}
    r = requests.get(url)
    # Insert error checking here
    return r.text

def get_stats(challenge):
    """Gets the status of a challenge and returns it as a view"""
    host = config.get(challenge, 'host')
    port = config.get(challenge, 'port')
    url = "http://%(host)s:%(port)s/stats" % {
        'host': host,
        'port': port}
    r = requests.get(url)
    return make_json_response(r.text)

def get_meta(challenge):
    """Gets the metadata of a challenge and returns it as a view"""
    host = config.get(challenge, 'host')
    port = config.get(challenge, 'port')
    url = "http://%(host)s:%(port)s/stats" % {
        'host': host,
        'port': port}
    r = requests.get(url)
    return make_json_response(r.text)

def post_task(challenge, task_id, form):
    """Handles the challenge posting proxy functionality"""
    host = config.get(challenge, 'host')
    port = config.get(challenge, 'port')
    url = "http://%(host)s:%(port)s/task/%(task_id)s" % {
        'host': host,
        'port': port,
        'task_id': task_id}
    r = requests.post(url, data = form)
    return make_json_response(r.text)

def filter_task(difficulty = 'easy', point = None):
    """Returns matching challenges based on difficulty and area"""
    chgs = []
    for name, challenge in challenges.items():
        if challenge['meta']['difficulty'] == difficulty:
            if point:
                if challenge['bounds'].contains(point):
                    chgs.append(name)
            else:
               chgs.append(name)
    return chgs

def task_distance(task_text, point):
    """Accepts a task and a point and returns the distance between them"""
    # First we must turn the task into an object from text
    task = geojson.loads(task_text)
    # Now find the selected feature
    for feature in task['features']:
        if feature['selected'] is True:
            geom = asShape(feature)
            return geom.distance(point)

def closest_task(chgs, point):
    """Returns the closest task by a list of challenges"""
    # Transform point into coordinates
    coords = point.coords
    lat, lon = coords[0]
    latlng = "%f,%f" % (lat, lon)
    tasks = [get_task(chg, latlng) for chg in chgs]
    sorted_tasks = sorted(tasks, key=lambda task: task_distance(task, point))
    return sorted_tasks[0]

def make_json_response(json):
    """Takes text and returns it as a JSON response"""
    return Response(json.encode('utf8'), 200, mimetype = 'application/json')

# By default, send out the standard client
@app.route('/')
def index():
    "Display the index.html"
    return render_template('index.html')

@app.route('/challenges.html')
def challenges_web():
    "Returns the challenges template"
    return render_template('challenges.html')

@app.route('/api/challenges')
def challenges_api():
    "Returns a list of challenges as json"
    chgs = [challenges[c].get('meta') for c in challenges]
    return jsonify({'challenges': chgs})

@app.route('/api/task')
def task():
    """Returns an appropriate task based on parameters"""
    # We need to find a task for the user to work on, based (as much
    # as possible)
    difficulty = request.args.get('difficulty', 'easy')
    near = request.args.get('near')
    if near:
        lat, lon = near.split(',')
        point = Point(lat, lon)
    else:
        point = None
    chgs = filter_task(difficulty, point)
    # We need code to test for an empty list here
    if near:
        task = closest_task(chgs, point)
    else:
        # Choose a random one
        challenge = choice(chgs)
        task = get_task(challenge)
    return make_json_response(task)
    
@app.route('/c/<challenge>/meta')
def challenge_meta(challenge):
    "Returns the metadata for a challenge"
    if challenge in challenges:
        return get_meta(challenge)
    else:
        return "No such challenge\n", 404

@app.route('/c/<challenge>/stats')
def challenge_stats(challenge):
    "Returns stat data for a challenge"
    if challenge in challenges:
        return get_stats(challenge)
    else:
        return "No such challenge\n", 404

@app.route('/c/<challenge>/task')
def challenge_task(challenge):
    "Returns a task for specified challenge"
    if challenge in challenges:
        task = get_task(get_task(challenge, request.args.get('near')))
        return make_json_response(task)
    else:
        return "No such challenge\n", 404

@app.route('/c/<challenge>/task/<id>', methods = ['POST'])
def challenge_post(challenge, task_id):
    "Accepts data for completed task"
    if challenge in challenges:
        dct = request.form
        return post_task(challenge, task_id, dct)
    else:
        return "No such challenge\n", 404


@app.route('/logout')
def logout():
    session.destroy()
    return redirect('/')

@app.route('/oauth/authorize')
def oauth_authorize():
    """Initiates OAuth authorization agains the OSM server"""
    return osm.authorize(callback=url_for('oauth_authorized',
      next=request.args.get('next') or request.referrer or None))

@app.route('/oauth/callback')
@osm.authorized_handler
def oauth_authorized(resp):
    """Receives the OAuth callback from OSM"""
    next_url = request.args.get('next') or url_for('index')
    if resp is None:
        return redirect(next_url)
    session['osm_token'] = (
      resp['oauth_token'],
      resp['oauth_token_secret']
    )
    print 'getting user data from osm'
    osmuserresp = osm.get('user/details')
    if osmuserresp.status == 200:
        session['user'] = get_user_attribs(osmuserresp.data)
    else:
        print 'not able to get osm user data'
    return redirect(next_url)

@app.route('/<path:path>')
def catch_all(path):
    "Returns static files based on path"
    return send_from_directory('static', path)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type = int, help = "the port to bind to",
                        default = 6666)
    parser.add_argument("--host", help = "the host to bind to",
                        default = "localhost")
    args = parser.parse_args()
    app.run(port=args.port)
    app.run(host=args.host, port=args.port)