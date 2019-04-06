import urllib2
import lxml.html
import glob
import subprocess
import time
import os
import threading
import logging
from socket import gethostbyname
from socket import gethostname
import pygame
import qrcode
from io import BytesIO
from signal import alarm, signal, SIGALRM, SIGKILL
import random
import sys
from subprocess import check_output

class Karaoke:

    overlay_file_path = "/tmp/pikaraoke-overlay.srt"

    queue = []
    available_songs = []
    now_playing = None
    process = None
    qr_code = None
    base_path = os.path.dirname(__file__)
    volume_offset = 0

    def __init__(self,
            port = 5000,
            download_path = '/usr/lib/pikaraoke/songs',
            hide_ip = False,
            hide_splash_screen = False,
            hide_overlay = False,
            volume = 0,
            log_level = logging.DEBUG,
            splash_delay = 2,
            youtubedl_path = '/usr/local/bin/youtube-dl',
            omxplayer_path ='/usr/bin/omxplayer'):

        #override with supplied constructor args if provided
        self.port = port
        self.hide_ip = hide_ip
        self.hide_splash_screen = hide_splash_screen
        self.splash_delay = int(splash_delay)
        self.hide_overlay = hide_overlay
        self.volume_offset = volume
        self.youtubedl_path = youtubedl_path
        self.player_path = omxplayer_path

        logging.basicConfig(format='[%(asctime)s] %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S', level=int(log_level))

        # setup download directory
        self.download_path = download_path
        if (not self.download_path.endswith('/')):
            self.download_path += '/'
        if not os.path.exists(self.download_path):
        	logging.info("Creating download path: " + self.download_path)
        	os.makedirs(self.download_path)

        logging.debug('''
    http port: %s
    hide IP: %s
    hide splash: %s
    splash_delay: %s
    hide overlay: %s
    download path: %s
    default volume: %s
    youtube-dl path: %s
    omxplayer path: %s
    log_level: %s'''
            % (self.port, self.hide_ip, self.hide_splash_screen,
            self.splash_delay, self.hide_overlay, self.download_path,
            self.volume_offset, self.youtubedl_path, self.player_path,
            log_level))

        # Generate connection URL and QR code, retry in case pi is still starting up
        # and doesn't have an IP yet (occurs when launched from /etc/rc.local)
        end_time = int(time.time()) + 30
        success = False
        while (int(time.time()) < end_time):
#            try:
#                self.ip = gethostbyname(gethostname())
#                success = True
#            except:
#                logging.debug("Could not get IP, retrying...")
#                time.sleep(2)
#            if success:
#                break
            self.ip = check_output(['hostname','-I']).strip()
            if (len(self.ip) < 7):
                logging.debug("Couldn't get IP, retrying....")
            else:
                break
        self.url = url = "http://%s:%s" % (self.ip, self.port)

        # get songs from download_path
        self.get_available_songs()

        # check binaries exist
        if (not os.path.isfile(self.youtubedl_path)):
            msg = "youtube-dl not found at: " + self.youtubedl_path
            logging.error(msg)
            sys.exit(msg)
        if (not os.path.isfile(self.player_path)):
            msg = "video player not found at: " + self.player_path
            logging.error(msg)
            sys.exit(msg)

        # clean up old sessions
        self.kill_player()
        if os.path.isfile(self.overlay_file_path):
            os.remove(self.overlay_file_path)

        if (not self.hide_splash_screen):
            self.initialize_screen()
            self.render_splash_screen()

    def generate_overlay_file(self,file_path):
        if (not self.hide_overlay):
            logging.debug("Generating overlay file")
            current_song = self.filename_from_path(file_path)
            if (not self.hide_ip):
                msg = "PiKaraoke IP: %s" % self.url
            else:
                msg = ""
            output = "00:00:00,00 --> 00:00:30,00 \n%s\n%s" % (current_song, msg)
            f = open(self.overlay_file_path, "w")
            f.write(output.encode('utf8'))
            logging.debug("Done generating overlay file: " + output)

    def generate_qr_code(self):
        logging.debug("Generating URL QR code")
        img = qrcode.make(self.url)
        qr_file = BytesIO()
        img.save(qr_file, 'png')
        qr_file.seek(0)
        return qr_file

    def initialize_screen(self):
        if (not self.hide_splash_screen):
            logging.debug("Initializing pygame")
            pygame.init()
            pygame.mouse.set_visible(0)
            self.font = pygame.font.SysFont(pygame.font.get_default_font(), 40)
            self.width = pygame.display.Info().current_w
            self.height = pygame.display.Info().current_h
            logging.debug("Initializing screen mode")

            # this section is an unbelievable nasty hack - for some reason Pygame
            # needs a keyboardinterrupt to initialise in some limited circumstances
            # source: https://stackoverflow.com/questions/17035699/pygame-requires-keyboard-interrupt-to-init-display
            class Alarm(Exception):
                pass
            def alarm_handler(signum, frame):
                raise Alarm
            signal(SIGALRM, alarm_handler)
            alarm(3)
            try:
                self.screen = pygame.display.set_mode([self.width,self.height],pygame.FULLSCREEN)
                alarm(0)
            except Alarm:
                raise KeyboardInterrupt
            logging.debug("Done initializing splash screen")

    def render_splash_screen(self):
        if (not self.hide_splash_screen):
            logging.debug("Rendering splash screen")

            self.screen.fill((0, 0, 0))

            logo = pygame.image.load(os.path.join(self.base_path, 'logo.png'))
            logo_rect = logo.get_rect(center = self.screen.get_rect().center)
            self.screen.blit(logo, logo_rect)

            if (not self.hide_ip):
                p_image = pygame.image.load(self.generate_qr_code())
                p_image = pygame.transform.scale(p_image, (150, 150))
                self.screen.blit(p_image, (0,0))
                text = self.font.render("Connect at: " + self.url, True, (255, 255, 255))
                self.screen.blit(text, (p_image.get_width() + 15, 0))

            pygame.display.flip()

    def render_next_song_to_splash_screen(self):
        if (not self.hide_splash_screen):
            self.render_splash_screen()
            if (len(self.queue) >= 2):
                logging.debug("Rendering next song to splash screen")
                next_song = self.filename_from_path(self.queue[1])
                font_next_song = pygame.font.SysFont(pygame.font.get_default_font(), 60)
                text = font_next_song.render("Up next:  " + next_song, True, (0, 128, 0))
                up_next = font_next_song.render("Up next:  ", True, (255, 255, 0))
                x = self.width - text.get_width() - 10
                y = self.height - text.get_height() - 5
                self.screen.blit(text,(x, y))
                self.screen.blit(up_next,(x, y))
                pygame.display.flip()
                time.sleep(self.splash_delay)
                return True
            else:
                logging.debug("Could not render next song to splash. No song in queue")
                return False

    def get_search_results(self, textToSearch):
        logging.info("Searching YouTube for: " + textToSearch)
        query = urllib2.quote(textToSearch)
        url = "https://www.youtube.com/results?search_query=" + query
        response = urllib2.urlopen(url,None,10)
        if (response):
	        html = response.read()
	        doc = lxml.html.fromstring(html)
	        elements = doc.xpath('//a[contains(@class,"yt-uix-tile-link")]')
	        results = []
	        for each in elements:
	            results.append({'title':each.get('title'), 'href':each.get('href')})
	        rc = []
	        for vid in results:
	            rc.append([vid['title'], 'https://www.youtube.com' + vid['href']])
	        return(rc)

        else:
        	logging.error("Failed to get response from: " + url)

    def get_karaoke_search_results(self, songTitle):
        return self.get_search_results(songTitle + " karaoke")

    def download_video(self, video_url, enqueue=False):
        logging.info("Downloading video: " + video_url)
        dl_path = self.download_path + "%(title)s---%(id)s.%(ext)s"
        cmd = [self.youtubedl_path,
        	'-f', 'mp4',
        	"-o", dl_path, video_url]
        logging.debug("Youtube-dl command: " + ' '.join(cmd))
        rc = subprocess.call(cmd)
        if (rc != 0):
        	logging.error("Error code while downloading, retrying once...")
        	rc = subprocess.call(cmd) #retry once. Seems like this can be flaky
        if (rc == 0):
            logging.debug("Song successfully downloaded: " + video_url)
            self.get_available_songs()
            if (enqueue):
            	y = self.get_youtube_id_from_url(video_url)
            	s = self.find_song_by_youtube_id(y)
            	if (s):
            		self.enqueue(s)
            	else:
            		logging.error("Error queueing song: " + video_url)
        else:
        	logging.error("Error downloading song: " + video_url)
        return rc

    def get_available_songs(self):
        logging.debug("Fetching available songs in: " + self.download_path)
        self.available_songs =  sorted(glob.glob(u'%s/*' % self.download_path))

    def delete(self, song_path):
        logging.info("Deleting song: " + song_path)
        os.remove(song_path)
        self.get_available_songs()

    def rename(self, song_path, new_name):
        logging.info("Renaming song: '" + song_path + "' to: " + new_name)
        ext = os.path.splitext(song_path)
        if (len(ext) == 2):
            new_name = new_name + ext[1]
        os.rename(song_path, self.download_path + new_name)

        self.get_available_songs()

    def filename_from_path(self, file_path):
	    rc = os.path.basename(file_path)
	    rc = os.path.splitext(rc)[0]
	    rc = rc.split("---")[0] #removes youtube id if present
	    return rc

    def find_song_by_youtube_id(self, youtube_id):
	    for each in self.available_songs:
	    	if (youtube_id in each):
	    		return each
	    logging.error("No available song found with youtube id: " + youtube_id)
	    return None

    def get_youtube_id_from_url(self, url):
		s = url.split("watch?v=")
		if (len(s) == 2):
			return s[1]
		else:
			logging.error("Error parsing youtube id from url: " + url)
	    	return NoneType

    def kill_player(self):
        logging.debug("Killing old omxplayer processes")
        player_kill = ["killall", "omxplayer.bin"]
        FNULL = open(os.devnull, 'w')
        subprocess.Popen(player_kill, stdin=subprocess.PIPE, stdout=FNULL, stderr=FNULL)

    def play_file(self, file_path):
        self.now_playing = self.filename_from_path(file_path)
        if (not self.hide_overlay):
        	self.generate_overlay_file(file_path)

        self.kill_player()

        logging.info("Playing video: " + self.now_playing)
        cmd = [self.player_path,file_path,
            "--blank",
            "-o", "both",
            "--vol", str(self.volume_offset),
            "--font-size", str(25)]
        if (not self.hide_overlay):
        	cmd += ["--subtitles", self.overlay_file_path]
        logging.debug("Player command: " + ' '.join(cmd))
        self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE,)
        self.render_splash_screen() # remove old previous track

    def is_file_playing(self):
        if (self.process == None):
            self.now_playing = None
            return False
        elif (self.process.poll() == None):
            return True
        else:
            self.now_playing = None
            return False

    def enqueue(self, song_path):
    	if (song_path in self.queue):
    		logging.warn("Song already in queue, will not add: " + song_path)
    		return False
    	else:
        	logging.info("Adding video to queue: " + song_path)
        	self.queue.append(song_path)
        	return True

    def queue_add_random(self, amount):
        logging.info("Adding %d random songs to queue" % amount)
        songs = list(self.available_songs) #make a copy
        if len(songs) == 0:
            logging.warn("No available songs!")
            return False
        i = 0
        while i < amount:
            r = random.randint(0,len(songs)-1)
            if songs[r] in self.queue:
                logging.warn("Song already in queue, trying another... " + songs[r])
            else:
                self.queue.append(songs[r])
                i += 1
            songs.pop(r)
            if (len(songs) == 0):
                logging.warn("Ran out of songs!")
                return False
        return True

    def queue_clear(self):
    	logging.info("Clearing queue!")
    	self.queue = []
    	self.skip()

    def queue_edit(self, song_name, action):
    	index = 0
    	song_path = None
    	for each in self.queue:
    		if song_name in each:
    			song_path = each
    			break
    		else:
    			index += 1
    	if (song_path == None):
    		logging.error("Song not found in queue: " + song_name)
    		return False
    	if (action == "up"):
    		if (index < 2):
    			logging.warn("Song is now playing or up next, can't bump up in queue: " + song_path)
    			return False
    		else:
    			logging.info("Bumping song up in queue: " + song_path)
    			del self.queue[index]
    			self.queue.insert(index-1, song_path)
    			return True
    	elif (action == "down"):
    		if (index == len(self.queue)-1):
    			logging.warn("Song is already last, can't bump down in queue: " + song_path)
    			return False
    		if (index == 0):
    			logging.warn("Song is currently playing, can't bump down in queue: " + song_path)
    			return False
    		else:
    			logging.info("Bumping song down in queue: " + song_path)
    			del self.queue[index]
    			self.queue.insert(index+1, song_path)
    			return True
    	elif (action == "delete"):
    		if (index == 0):
    			self.skip()
    			logging.warn("Song is currently playing, skipping: " + song_path)
    			return True
    		else:
    			logging.info("Deleting song from queue: " + song_path)
    			del self.queue[index]
    			return True
    	else:
    		logging.error("Unrecognized direction: " + direction)
    		return False

    def skip(self):
        if (self.is_file_playing()):
            logging.info("Skipping: " + self.now_playing)
            self.process.stdin.write("q")
            self.now_playing = None
            return True
        else:
            logging.warning("Tried to skip, but no file is playing!")
            return False

    def pause(self):
        if (self.is_file_playing()):
            logging.info("Pausing: " + self.now_playing)
            self.process.stdin.write("p")
            return True
        else:
            logging.warning("Tried to pause, but no file is playing!")
            return False

    def vol_up(self):
        if (self.is_file_playing()):
            logging.info("Volume up: " + self.now_playing)
            self.process.stdin.write("=")
            self.volume_offset += 300
            return True
        else:
            logging.warning("Tried to volume up, but no file is playing!")
            return False

    def vol_down(self):
        if (self.is_file_playing()):
            logging.info("Volume down: " + self.now_playing)
            self.process.stdin.write("-")
            self.volume_offset -= 300
            return True
        else:
            logging.warning("Tried to volume down, but no file is playing!")
            return False

    def restart(self):
        if (self.is_file_playing()):
            logging.info("Restarting: " + self.now_playing)
            self.process.stdin.write("i")
            return True
        else:
            logging.warning("Tried to restart, but no file is playing!")
            return False

    def run(self):
        logging.info("Starting PiKaraoke!")
        while True:
            if (len(self.queue) == 0):
                # wait for queue to contain something
                time.sleep(1)
            else:
                while (len(self.queue) > 0):
                    vid = self.queue[0]
                    self.play_file(vid)
                    while (self.is_file_playing()):
                        # wait for file to complete
                        time.sleep(1)
                    self.render_next_song_to_splash_screen()
                    if (self.queue and len(self.queue) > 0):
                    	# remove first song from queue
                        self.queue.pop(0)
