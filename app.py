from flask import Flask
from flask import render_template
from flask import request
from flask import redirect
from flask import url_for
from flask import send_from_directory
from flask import send_file
from flask import flash
from urllib import quote
from urllib import unquote
import karaoke
import threading
import sys
import logging
import os
import json
import cherrypy
import argparse
import psutil

app = Flask(__name__)
app.secret_key = 'HjI981293u99as811lll'
site_name = "PiKaraoke"

def filename_from_path(file_path,remove_youtube_id=True):
    rc = os.path.basename(file_path)
    rc = os.path.splitext(rc)[0]
    if (remove_youtube_id):
        rc = rc.split("---")[0] #removes youtube id if present
    return rc

def url_escape(filename):
    return quote(filename.encode('utf8'))

@app.route("/")
def home():
    return render_template('home.html', site_title = site_name, title='Home')

@app.route("/nowplaying")
def nowplaying():
    if (k.now_playing != None):
        if (len(k.queue) >=2 ):
            next_song = filename_from_path(k.queue[1])
        else:
            next_song = None
        rc = {'now_playing' : k.now_playing, 'up_next' : next_song}
        return json.dumps(rc)
    else:
        return ""

@app.route("/queue")
def queue():
    return render_template('queue.html', queue = k.queue, site_title = site_name,
        title='Queue')
        
@app.route("/queue/edit", methods=['GET'])
def queue_edit():
    action = request.args['action']
    if (action == "clear"):
        k.queue_clear()
        flash("Cleared the queue!", "is-warning")
        return redirect(url_for('queue'))
    else:
        song = request.args['song']
        song = unquote(song)
        if (action == "down"):
            result = k.queue_edit(song, "down")
            if (result):
                flash("Moved down in queue: " + song, "is-success")
            else:
                flash("Error moving down in queue: " + song, "is-danger")
        elif (action == "up"):
            result = k.queue_edit(song, "up")
            if (result):
                flash("Moved up in queue: " + song, "is-success")
            else:
                flash("Error moving up in queue: " + song, "is-danger")
        elif (action == "delete"):
            result = k.queue_edit(song, "delete")
            if (result):
                flash("Deleted from queue: " + song, "is-success")
            else:
                flash("Error deleting from queue: " + song, "is-danger")
    return redirect(url_for('queue'))
        
@app.route("/enqueue", methods=['POST','GET'])
def enqueue():
    if (request.args.has_key('song')):
        song = request.args['song']
    else:
        d = request.form.to_dict()
        song = d['song_to_add']
    rc = k.enqueue(song)
    if (rc):
        flash('Song added to queue: ' + filename_from_path(song), "is-success")
    else:
        flash('Song is already in queue: ' + filename_from_path(song), "is-danger")
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
    rc = k.restart()
    return redirect(url_for('home'))

@app.route("/vol_up")
def vol_up():
    rc = k.vol_up()
    return redirect(url_for('home'))
    
@app.route("/vol_down")
def vol_down():
    k.vol_down()
    return redirect(url_for('home'))

@app.route("/search", methods=['GET'])
def search():
    if (request.args.has_key('search_string')):
        search_string = request.args['search_string']
        search_results = k.get_karaoke_search_results(search_string)
    else:
        search_results = None
    return render_template('search.html', site_title = site_name, title='Search',
        songs=k.available_songs, search_results = search_results)
        
@app.route("/browse", methods=['GET'])
def browse():
    if (request.args.has_key('sort') and request.args['sort'] == "date"):
        songs = sorted(k.available_songs, key = lambda x: os.path.getctime(x))
        songs.reverse()
        sort_order = "Date"
    else:
        songs = k.available_songs
        sort_order = "Alphabetical"
    return render_template('files.html', sort_order=sort_order, site_title = site_name, 
        title='Browse', songs=songs)
        
@app.route("/download", methods=['POST'])
def download():
    d = request.form.to_dict()
    song = d['song-url']
    if (d.has_key('queue') and d['queue'] == "on"):
        queue = True 
    else: 
        queue = False

    #download in the background since this can take a few minutes
    t = threading.Thread(target= k.download_video,args=[song,queue])
    t.daemon = True
    t.start()
    
    flash_message = "Download started: '" + song + "'. This may take a couple of minutes to complete. "
 
    if (queue):
    	flash_message += 'Song will be added to queue.'
    else:
    	flash_message += 'Song will appear in the "available songs" list.'
    flash(flash_message, "is-info")
    return redirect(url_for('search'))
    
@app.route('/qrcode')
def qrcode():
    return send_file(k.generate_qr_code(), mimetype='image/png')

@app.route('/files/delete', methods=['GET'])
def delete_file():
    if (request.args.has_key('song')):
        song_path = request.args['song']
        if song_path in k.queue:
            flash("Error: Can't delete this song because it is in the current queue: " + song_path, "is-danger")
        else:
            k.delete(song_path)
            flash("Song deleted: " + song_path, "is-warning")
    else:
        flash("Error: No song parameter specified!", "is-danger")
    return redirect(url_for('browse'))
    
@app.route('/files/edit', methods=['GET', 'POST'])
def edit_file():
    queue_error_msg = "Error: Can't edit this song because it is in the current queue: "
    if (request.args.has_key('song')):
        song_path = request.args['song']
        #print "SONG_PATH" + song_path
        if song_path in k.queue:
            flash(queue_error_msg + song_path, "is-danger")
            return redirect(url_for('browse'))
        else:
            return render_template('edit.html', site_title = site_name, 
                title='Song File Edit', song=song_path.encode('utf-8'))
    else:
        d = request.form.to_dict()
        if (d.has_key('new_file_name') and d.has_key('old_file_name')):
            new_name = d['new_file_name']
            old_name = d['old_file_name']
            if old_name in k.queue:
                # check one more time just in case someone added it during editing
                flash(queue_error_msg + song_path, "is-danger")
            else: 
                k.rename(old_name, new_name)
                flash("Renamed file: '%s' to '%s'." % (old_name, new_name), "is-warning")
        else: 
            flash("Error: No filename parameters were specified!", "is-danger")
        return redirect(url_for('browse'))

@app.route("/info")
def info():
    url = "http://" + request.host
    
    #cpu
    cpu = str(psutil.cpu_percent()) + '%'
    
    # mem
    memory = psutil.virtual_memory()
    available = round(memory.available/1024.0/1024.0,1)
    total = round(memory.total/1024.0/1024.0,1)
    memory = str(available) + 'MB free / ' + str(total) + 'MB total ( ' + str(memory.percent) + '% )'
    
    #disk
    disk = psutil.disk_usage('/')
    # Divide from Bytes -> KB -> MB -> GB
    free = round(disk.free/1024.0/1024.0/1024.0,1)
    total = round(disk.total/1024.0/1024.0/1024.0,1)
    disk = str(free) + 'GB free / ' + str(total) + 'GB total ( ' + str(disk.percent) + '% )'
    
    return render_template('info.html', site_title = site_name, title='Info',
        url=url, memory=memory, cpu=cpu, disk=disk )
        
if __name__ == '__main__':

    # parse CLI args
    parser = argparse.ArgumentParser()
    default_port = 5000
    parser.add_argument('-p','--port', help='Desired http port (default: %d)' % default_port, default=default_port, required=False)
    default_dl_dir = os.getcwd() + '/songs'
    parser.add_argument('-d','--download-path', help='Desired path for downloaded songs. (default: %s)' % default_dl_dir, default=default_dl_dir, required=False)
    args = parser.parse_args()
    
    app.jinja_env.globals.update(filename_from_path=filename_from_path)
    app.jinja_env.globals.update(url_escape=quote)

    # Start karaoke process
    global k 
    k = karaoke.Karaoke(port=args.port, download_path=args.download_path)
    t = threading.Thread(target=k.run)
    t.daemon = True
    t.start()

    cherrypy.tree.graft(app, '/')
    # Set the configuration of the web server
    cherrypy.config.update({
        'engine.autoreload_on': False,
        'log.screen': True,
        'server.socket_port': int(args.port),
        'server.socket_host': '0.0.0.0'
    })

    # Start the CherryPy WSGI web server
    cherrypy.engine.start()
    cherrypy.engine.block()
    exit(0)
