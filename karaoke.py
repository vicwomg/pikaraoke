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

class Karaoke:

    #default paths
    #download_path = "/home/pi/pikaraoke/songs"
    youtube_dl_path = "/usr/local/bin/youtube-dl"
    player_path = "/usr/bin/omxplayer"
    overlay_file_path = "/tmp/overlay.srt" # text overlay that will show on top of videos

    queue = []
    available_songs = []
    now_playing = None
    process = None
    show_overlay = True
    port = "<unknown_port>"

    log_level = logging.DEBUG
    logging.basicConfig(format='[%(asctime)s] %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S', level=log_level)

    def __init__(self, port=5000, download_path=os.getcwd() + '/songs'):
        
        #override with supplied constructor args if provided
        self.port = port
        logging.info("Port is: %s" % self.port)
        
        # setup download directory
        self.download_path = download_path
        if (not self.download_path.endswith('/')):
            self.download_path += '/'
        if not os.path.exists(self.download_path):
        	logging.info("Creating download path: " + self.download_path)
        	os.makedirs(self.download_path)
        	
        # Generate connection URL and QR code
        self.ip = gethostbyname(gethostname())
        self.url = url = "http://%s:%s" % (self.ip, self.port)
        self.generate_qr_code()
        
        # get songs from download_path
        self.get_available_songs()
        
        if (self.show_overlay):
        	self.generate_pikaraoke_overlay_file()

        self.initialize_screen()
        self.render_splash_screen()
    
    def generate_pikaraoke_overlay_file(self):
	    output = "00:00:00,00 --> 00:00:30,00 \nConnect at: %s" % self.url
	    f = open(self.overlay_file_path, "w")
	    f.write(output)
	
    def generate_qr_code(self):
        logging.debug("Generating URL QR code")
        img = qrcode.make(self.url)
        qr_file = BytesIO()
        img.save(qr_file, 'png')
        qr_file.seek(0)
        return qr_file
        
    def initialize_screen(self):
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
        logging.debug("Rendering splash screen")
        p_image = pygame.image.load(self.generate_qr_code())
        p_image = pygame.transform.scale(p_image, (150, 150))   
        
        text = self.font.render("Connect to PiKaraoke: " + self.url, True, (0, 0, 0)) 
        self.screen.fill((255, 255, 255))
        self.screen.blit(text,(10, self.height - text.get_height() - 5))
        self.screen.blit(p_image, (0,0))
        logo = pygame.image.load('./logo.jpg')
        logo_rect = logo.get_rect(center = self.screen.get_rect().center)
        self.screen.blit(logo, logo_rect)
        pygame.display.flip()

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
        cmd = [self.youtube_dl_path,
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
        self.available_songs = sorted(glob.glob(self.download_path + "/*"))

    def delete(self, song_path):
        logging.info("Deleting song: " + song_path)
        os.remove(song_path)
        self.get_available_songs()
        
    def rename(self, song_path, new_name):
        logging.info("Renaming song: '" + song_path + "' to: " + new_name)
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
	    	return None

    def play_file(self, file_path):
        self.now_playing = self.filename_from_path(file_path)
        logging.info("Playing video: " + self.now_playing)
        cmd = [self.player_path,file_path, "--blank"]
        if self.show_overlay:
        	cmd += ["--subtitles", self.overlay_file_path]
        logging.debug("Player command: " + ' '.join(cmd))
        self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE,)

    def is_file_playing(self):
        if (self.process.poll() == None):
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
    			logging.warn("Song is currently playing, can't delete from queue: " + song_path)
    			return False
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
        else:
            logging.warning("Tried to skip, but no file is playing!")
            
    def pause(self):
        if (self.is_file_playing()):
            logging.info("Pausing: " + self.now_playing)
            self.process.stdin.write("p")
        else:
            logging.warning("Tried to pause, but no file is playing!")
            
    def vol_up(self):
        if (self.is_file_playing()):
            logging.info("Volume up: " + self.now_playing)
            self.process.stdin.write("=")
        else:
            logging.warning("Tried to volume up, but no file is playing!")
            
    def vol_down(self):
        if (self.is_file_playing()):
            logging.info("Volume down: " + self.now_playing)
            self.process.stdin.write("-")
        else:
            logging.warning("Tried to volume down, but no file is playing!")
            
    def restart(self):
        if (self.is_file_playing()):
            logging.info("Restarting: " + self.now_playing)
            self.process.stdin.write("i")
        else:
            logging.warning("Tried to restart, but no file is playing!")

    def run(self):
        logging.info("Starting Karaoke!")
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
                    if (self.queue and len(self.queue) > 0):
                    	# remove first song from queue
                        self.queue.pop(0)
