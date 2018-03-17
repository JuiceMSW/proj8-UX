"""
Replacement for RUSA ACP brevet time calculator
(see https://rusa.org/octime_acp.html)

"""

import flask
from flask import *
from flask.ext.login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from wtforms import *
from pymongo import *
import arrow  # Replacement for datetime, based on moment.js
import acp_times  # Brevet time calculations
import config
import datetime

from urllib.parse import urlparse, urljoin

from passlib.apps import custom_app_context as pwd_context
from itsdangerous import (TimedJSONWebSignatureSerializer \
                                  as Serializer, BadSignature, \
                                  SignatureExpired)

import logging

###
# Globals
###
app = flask.Flask(__name__)
CONFIG = config.configuration()
app.secret_key = CONFIG.SECRET_KEY

login_manager = LoginManager()
login_manager.init_app(app)

client = MongoClient(CONFIG.MONGO_URI)
db = client.get_default_database()
brevet_times_col = db['brevet_times']
user_col = db["users"]

###
# Pages
###

allUsers = []

@login_manager.user_loader
def load_user(user_id):
    for user in allUsers:
        if user_id == user.get_id():
            return user
    return None


class User(UserMixin):
    username = ""
    id = ""


class LoginForm(Form):
    username = TextField("Username", [validators.Required()])
    password = PasswordField("Password", [validators.Required()])
    remember = BooleanField("Remember Me?") 

def verify_user(username, password):
    match = False
    hashed_password = None

    users = user_col.find({})
    for entries in users:
        if entries["un"] == username:
            uuid = str(entries["_id"])
            hashed_password = entries["password"]
            match = True
            break

    if match == False:
        return False

    if verify_password(password, hashed_password):
        return True
        
    return False

# From http://flask.pocoo.org/snippets/62/
def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and \
        ref_url.netloc == test_url.netloc

form = LoginForm()

@app.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm(request.form)
    if request.method == "POST" and form.validate():
        if verify_user(form.username.data, form.password.data):
            user = User()
            uuid = None
            user.username = form.username.data

            users = user_col.find({})
            for entries in users:
                if entries["un"] == user.username:
                    uuid = entries["_id"]
                    break

            user.id = uuid

            global allUsers
            allUsers.append(user)

            login_user(user)

            # https://stackoverflow.com/a/36284881
            dest = flask.request.args.get('next')

            if dest == None:
                if not form.remember.data:
                    return flask.redirect(flask.url_for("calc"))
                else:
                   # http://flask.pocoo.org/snippets/30/
                   response = flask.make_response(flask.render_template('calc.html'))
                   response.set_cookie('token', session['token'])
                   return response

            if not is_safe_url(dest):
                return flask.abort(400)
            else:
               return flask.redirect(dest)
        return flask.redirect(flask.url_for("index"))
    return flask.redirect(flask.url_for("index"))

@app.login_manager.unauthorized_handler
def unauth_handler():
    return flask.redirect(flask.url_for("index"))

@app.route("/")
@app.route("/index")
def index():
    return flask.render_template("login.html", form=LoginForm()), 200

@app.route("/calc")
def calc():
    return flask.render_template("calc.html"), 200

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return flask.redirect(flask.url_for("index"))


@app.errorhandler(404)
def page_not_found(error):
    app.logger.debug("Page not found")
    flask.session['linkback'] = flask.url_for("index")
    return flask.render_template('404.html'), 404


###############
#
# AJAX request handlers
#   These return JSON, rather than rendering pages.
#
###############
@app.route("/_calc_times")
def _calc_times():
    """
    Calculates open/close times from miles, using rules
    described at https://rusa.org/octime_alg.html.
    Expects one URL-encoded argument, the number of miles.
    """
    app.logger.debug("Got a JSON request")
    km = request.args.get('km', 999, type=float)
    brevet = request.args.get('brevet', type=int)
    start_info = request.args.get('start_info', type=str)
    app.logger.debug("km={}".format(km))
    app.logger.debug("request.args: {}".format(request.args))
    open_time = acp_times.open_time(km, brevet, start_info)
    close_time = acp_times.close_time(km, brevet, start_info)
    result = {"open": open_time, "close": close_time}
    return flask.jsonify(result=result)

@app.route("/_submit_times_db")
@login_required
def _submit_times_db():
    username = request.args.get("username", type=str)

    brevet_times_col.delete_many({"un": username})

    miles = request.args.get("miles", type=str).split("|")
    km = request.args.get("km", type=str).split("|")
    openTime = request.args.get("open", type=str).split("|")
    closeTime = request.args.get("close", type=str).split("|")

    num_controls = len(miles)

    print(miles)
    print(km)
    print(openTime)
    print(closeTime)

    for control in range(num_controls - 1):
        brevet_times_col.insert({
                                "un": username,
                                "miles": miles[control],
                                "km": km[control],
                                "openTime": openTime[control],
                                "closeTime": closeTime[control]
                                })
    return ""

@app.route("/listAll")
@app.route("/listAll/json")
@login_required
def json_listAll():
    username = request.args.get("username", default = "", type=str)
    k = request.args.get("top", default = -1, type=int)
    controls = brevet_times_col.find({})
    containerString = "<html>"
    containerString += '{<br/>&emsp;"results" : [<br/>'
    openTimes = []
    closeTimes = []
    for entries in controls:
        if entries["un"] == username:
            openTimes.append(entries['openTime'])
            closeTimes.append(entries['closeTime'])
    if k != -1:
        # Sorting Code from : https://stackoverflow.com/a/17627575
        openTimes.sort(key=lambda x: datetime.datetime.strptime(x, ' %m/%d %H:%M'))
        closeTimes.sort(key=lambda x: datetime.datetime.strptime(x, ' %m/%d %H:%M'))
    for i in range(len(openTimes)):
        if i == k: #Break when i = k to only display top k results.  If for loop went from 0 to k, if k > len(openTimes) it would error, this prevents that
            break
        containerString += "&emsp;&emsp;{"
        containerString += '<br/>&emsp;&emsp;&emsp;"openTime" : ' + openTimes[i] + ",<br/>&emsp;&emsp;&emsp;" + '"closeTime" : ' + closeTimes[i] + "<br/>"
        containerString += "&emsp;&emsp;},<br/>"
    containerString = containerString[:-6]
    containerString += "<br/>&emsp;]<br/>}"
    containerString += "</html>"
    return flask.jsonify(result=containerString)

@app.route("/listOpenOnly")
@app.route("/listOpenOnly/json")
@login_required
def json_listOpenOnly():
    username = request.args.get("username", default = "", type=str)
    k = request.args.get("top", default = -1, type=int)
    controls = brevet_times_col.find({})
    containerString = "<html>"
    containerString += '{<br/>&emsp;"results" : [<br/>'
    openTimes = []
    for entries in controls:
        if entries["un"] == username:
            openTimes.append(entries['openTime'])
    if k != -1:
        # Sorting Code from : https://stackoverflow.com/a/17627575
        openTimes.sort(key=lambda x: datetime.datetime.strptime(x, ' %m/%d %H:%M'))
    for i in range(len(openTimes)):
        if i == k: #Break when i = k to only display top k results.  If for loop went from 0 to k, if k > len(openTimes) it would error, this prevents that
            break
        containerString += "&emsp;&emsp;{"
        containerString += '<br/>&emsp;&emsp;&emsp;"openTime" : ' + openTimes[i] + "<br/>"
        containerString += "&emsp;&emsp;},<br/>"
    containerString = containerString[:-6]
    containerString += "<br/>&emsp;]<br/>}"
    containerString += "</html>"
    return flask.jsonify(result=containerString)

@app.route("/listCloseOnly")
@app.route("/listCloseOnly/json")
@login_required
def json_listCloseOnly():
    username = request.args.get("username", default = "", type=str)
    k = request.args.get("top", default = -1, type=int)
    controls = brevet_times_col.find({})
    containerString = "<html>"
    containerString += '{<br/>&emsp;"results" : [<br/>'
    openTimes = []
    closeTimes = []
    for entries in controls:
        if entries["un"] == username:
            closeTimes.append(entries['closeTime'])
    if k != -1:
        # Sorting Code from : https://stackoverflow.com/a/17627575
        closeTimes.sort(key=lambda x: datetime.datetime.strptime(x, ' %m/%d %H:%M'))
    for i in range(len(closeTimes)):
        if i == k: #Break when i = k to only display top k results.  If for loop went from 0 to k, if k > len(openTimes) it would error, this prevents that
            break
        containerString += "&emsp;&emsp;{"
        containerString += '<br/>&emsp;&emsp;&emsp;"closeTime" : ' + closeTimes[i] + "<br/>"
        containerString += "&emsp;&emsp;},<br/>"
    containerString = containerString[:-6]
    containerString += "<br/>&emsp;]<br/>}"
    containerString += "</html>"
    return flask.jsonify(result=containerString)

@app.route("/listAll/csv")
@login_required
def csv_listAll():
    username = request.args.get("username", default = "", type=str)
    k = request.args.get("top", default = -1, type=int)
    controls = brevet_times_col.find({})
    containerString = "<html>Open, Close<br/>"
    openTimes = []
    closeTimes = []
    for entries in controls:
        if entries["un"] == username:
            openTimes.append(entries['openTime'])
            closeTimes.append(entries['closeTime'])
    if k != -1:
        # Sorting Code from : https://stackoverflow.com/a/17627575
        openTimes.sort(key=lambda x: datetime.datetime.strptime(x, ' %m/%d %H:%M'))
        closeTimes.sort(key=lambda x: datetime.datetime.strptime(x, ' %m/%d %H:%M'))
    for i in range(len(openTimes)):
        if i == k: #Break when i = k to only display top k results.  If for loop went from 0 to k, if k > len(openTimes) it would error, this prevents that
            break
        containerString += openTimes[i] + ", " + closeTimes[i] + "<br/>"
    containerString += "</html>"
    return flask.jsonify(result=containerString)

@app.route("/listOpenOnly/csv")
@login_required
def csv_listOpenOnly():
    username = request.args.get("username", default = "", type=str)
    k = request.args.get("top", default = -1, type=int)
    controls = brevet_times_col.find({})
    containerString = "<html>Open<br/>"
    openTimes = []
    for entries in controls:
        if entries["un"] == username:
            openTimes.append(entries['openTime'])
    if k != -1:
        # Sorting Code from : https://stackoverflow.com/a/17627575
        openTimes.sort(key=lambda x: datetime.datetime.strptime(x, ' %m/%d %H:%M'))
    for i in range(len(openTimes)):
        if i == k: #Break when i = k to only display top k results.  If for loop went from 0 to k, if k > len(openTimes) it would error, this prevents that
            break
        containerString += openTimes[i] + "<br/>"
    containerString += "</html>"
    return flask.jsonify(result=containerString)

@app.route("/listCloseOnly/csv")
@login_required
def csv_listCloseOnly():
    username = request.args.get("username", default = "", type=str)
    k = request.args.get("top", default = -1, type=int)
    controls = brevet_times_col.find({})
    containerString = "<html>Close<br/>"
    closeTimes = []
    for entries in controls:
        if entries["un"] == username:
            closeTimes.append(entries['closeTime'])
    if k != -1:
        # Sorting Code from : https://stackoverflow.com/a/17627575
        closeTimes.sort(key=lambda x: datetime.datetime.strptime(x, ' %m/%d %H:%M'))
    for i in range(len(closeTimes)):
        if i == k: #Break when i = k to only display top k results.  If for loop went from 0 to k, if k > len(openTimes) it would error, this prevents that
            break
        containerString += closeTimes[i] + "<br/>"
    containerString += "</html>"
    return flask.jsonify(result=containerString)

# Used in registering user
def hash_password(password):
    return pwd_context.hash(password)

@app.route("/api/register")
def _register_user():
    username = request.args.get("username", default = "", type=str)
    plain_password = request.args.get("password", default = "", type=str)
    users = user_col.find({})
    for entries in users:
        if entries["un"] == username:
            result = {"Location": "", "username": "", "password": ""}
            abort(400)
    hashed_password = hash_password(plain_password)
    plain_password = None
    user_col.insert({
                    "un": username,
                    "password": hashed_password
                    })
    location = None
    for entries in users:
        if entries["un"] == username:
            location = entries["_id"]
            break
    result = {"Location": location, "username": username, "password": hashed_password}
    return flask.jsonify(result=result), 201

# Used in basic HTTP authentication
def verify_password(password, hashVal):
    return pwd_context.verify(password, hashVal)

def generate_auth_token(_id, expiration=600):
    s = Serializer(app.config['SECRET_KEY'], expires_in=expiration)
    return s.dumps({"id": _id})

@app.route("/api/token")
def _return_token():
    username = request.args.get("username", default = "", type=str)
    password = request.args.get("password", default = "", type=str)
    match = False
    uuid = None
    hashed_password = None

    users = user_col.find({})
    for entries in users:
        if entries["un"] == username:
            uuid = str(entries["_id"])
            hashed_password = entries["password"]
            match = True
            break

    if match == False:
        abort(401)

    if verify_password(password, hashed_password):
        token = generate_auth_token(uuid, 600)
        session['token'] = token
        result = {'token': str(token), 'duration': 600}
        return flask.jsonify(result=result)
    else:
        abort(401)

# Used in every important redirect to make sure it is protected
def verify_auth_token(token):
    s = Serializer(app.config['SECRET_KEY'])
    try:
        data = s.loads(token)
    except SignatureExpired:
        return None    # valid token, but expired
    except BadSignature:
        return None    # invalid token
    return "Success"

#############

app.debug = CONFIG.DEBUG
if app.debug:
    app.logger.setLevel(logging.DEBUG)

if __name__ == "__main__":
    print("Opening for global access on port {}".format(CONFIG.PORT))
    app.run(port=CONFIG.PORT, host="0.0.0.0")
