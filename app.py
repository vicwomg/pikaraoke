from flask import Flask
from flask import render_template
from flask import request
from flask import redirect
from flask import url_for
from flask import send_from_directory
from flask import flash
import karaoke
import threading
import sys
import logging
import os

app = Flask(__name__)
app.secret_key = 'HjI981293u99as811lll'

reload(sys)
sys.setdefaultencoding('utf-8')
site_name = "PiKaraoke"

k = karaoke.Karaoke()
t = threading.Thread(target=k.run)
t.daemon = True
t.start()

log_level = logging.DEBUG
logging.basicConfig(format='%(levelname)s: %(message)s', level=log_level)

def filename_from_path(file_path):
    rc = os.path.basename(file_path)
    rc = os.path.splitext(rc)[0]
    rc = rc.split("---")[0] #removes youtube id if present
    return rc

app.jinja_env.globals.update(filename_from_path=filename_from_path)

@app.route("/")
def home():
    files = k.get_available_songs()
    songs = []
    #add titles to songs
    for each in files:
        title = k.filename_from_path(each)
        songs.append([title, each])
    return render_template('home.html', songs = songs,
        now_playing = k.now_playing, queue = k.queue, site_title = site_name,
        title='Home')

@app.route("/nowplaying")
def nowplaying():
    if (k.now_playing != None):
        return k.now_playing
    else:
        return ""

@app.route("/queue")
def queue():
    return render_template('queue.html', queue = k.queue, site_title = site_name,
        title='Queue')

@app.route("/enqueue", methods=['POST'])
def enqueue():
    d = request.form.to_dict()
    song = d['song_to_add']
    k.enqueue(song)
    flash('Song added to queue: ' + song)
    return redirect(url_for('home'))

@app.route("/skip")
def skip():
    k.skip()
    return redirect(url_for('home'))
    
@app.route("/pause")
def pause():
    k.pause()
    return redirect(url_for('home'))
    
@app.route("/restart")
def restart():
    k.restart()
    return redirect(url_for('home'))

@app.route("/search", methods=['GET'])
def search():
    if (request.args.has_key('search_string')):
        search_string = request.args['search_string']
        search_results = k.get_karaoke_search_results(search_string)
    else:
        search_results = None
    return render_template('search.html', site_title = site_name, title='Search',
        songs=k.get_available_songs(), search_results = search_results)

@app.route("/download", methods=['POST'])
def download():
    d = request.form.to_dict()
    song = d['song-url']
    if (d.has_key('queue') and d['queue'] == "on"):
        queue = True 
    else: 
        queue = False
    #print("%s  - %s - %s" % (song, title, queue))
    t = threading.Thread(target= k.download_video,args=[song,queue])
    t.daemon = True
    t.start()
    
    flash('Download started for: "' + song + '"')
    flash('This may take a couple of minutes to complete...')
 
    if (queue):
    	flash('Song will be added to queue when complete.')
    else:
    	flash('Song will be available in the "available songs" list complete.')
    return redirect(url_for('search'))
    # return render_template('search.html', site_title = site_name, title='Search',
#         songs=k.get_available_songs(), search_results = None)
