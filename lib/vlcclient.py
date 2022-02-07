import os, sys, re, random, shutil
import string, logging, time
import subprocess, zipfile
from threading import Timer

import requests

from lib.get_platform import get_platform


def get_default_vlc_path(platform):
	if platform == "osx":
		return "/Applications/VLC.app/Contents/MacOS/VLC"
	elif platform == "windows":
		alt_vlc_path = r"C:\\Program Files (x86)\\VideoLAN\VLC\\vlc.exe"
		if os.path.isfile(alt_vlc_path):
			return alt_vlc_path
		else:
			return r"C:\Program Files\VideoLAN\VLC\vlc.exe"
	else:
		return shutil.which('cvlc') or shutil.which('vlc')


class VLCClient:
	vol_increment = 10

	def __init__(self, port = 5002, path = None, qrcode = None, url = None):

		# HTTP remote control server
		self.http_password = "".join(
			[random.choice(string.ascii_letters + string.digits) for n in range(32)]
		)
		self.port = port
		self.http_endpoint = "http://localhost:%s/requests/status.xml" % self.port
		self.http_command_endpoint = self.http_endpoint + "?command="
		self.is_transposing = False

		self.qrcode = qrcode
		self.url = url

		# Handle vlc paths
		self.platform = get_platform()
		if path == None:
			self.path = get_default_vlc_path(self.platform)
		else:
			self.path = path

		# Determine tmp directories (for things like extracted cdg files)
		if self.platform == "windows":
			self.tmp_dir = os.path.expanduser(r"~\\AppData\\Local\\Temp\\pikaraoke\\")
		else:
			self.tmp_dir = "/tmp/pikaraoke/"

		# Set up command line args
		self.cmd_base = [
			self.path,
			"-f",
			"--play-and-exit",
			"--extraintf",
			"http",
			"--http-port",
			"%s" % self.port,
			"--http-password",
			self.http_password,
			"--no-embedded-video",
			"--no-keyboard-events",
			"--no-mouse-events",
			"--video-on-top",
			"--no-video-title",
			"--no-loop",
			"--no-repeat",
			"--mouse-hide-timeout",
			"0",
		]
		if self.platform == "osx":
			self.cmd_base += [
				"--no-macosx-show-playback-buttons",
				"--no-macosx-show-playmode-buttons",
				"--no-macosx-interfacestyle",
				"--macosx-nativefullscreenmode",
				"--macosx-continue-playback",
				"0",
			]
		if self.qrcode and self.url:
			self.cmd_base += self.get_marquee_cmd()

		logging.info("VLC command base: " + " ".join(self.cmd_base))

		self.volume_offset = 10
		self.process = None
		self.last_status_text = ""
		self.last_status_time = time.time()

	def get_marquee_cmd(self):
		return ["--sub-source", 'logo{file=%s,position=9,x=2,opacity=200}:marq{marquee="Pikaraoke - connect at: \n%s",position=9,x=38,color=0xFFFFFF,size=11,opacity=200}' % (self.qrcode, self.url)]

	def handle_zipped_cdg(self, file_path):
		extracted_dir = os.path.join(self.tmp_dir, "extracted")
		if (os.path.exists(extracted_dir)):
			shutil.rmtree(extracted_dir)
		with zipfile.ZipFile(file_path, 'r') as zip_ref:
			zip_ref.extractall(extracted_dir)

		mp3_file = None
		cdg_file = None
		files = os.listdir(extracted_dir)
		for file in files:
			ext = os.path.splitext(file)[1]
			if ext.casefold() == ".mp3":
				mp3_file = file
			elif ext.casefold() == ".cdg":
				cdg_file = file

		if (mp3_file is not None) and (cdg_file is not None):
			if (os.path.splitext(mp3_file)[0] == os.path.splitext(cdg_file)[0]):
				return os.path.join(extracted_dir, mp3_file)
			else:
				raise Exception("Zipped .mp3 file did not have a matching .cdg file: " + files)
		else:
			raise Exception("No .mp3 or .cdg was found in the zip file: " + file_path)

	def handle_mp3_cdg(self, file_path):
		f = os.path.splitext(os.path.basename(file_path))[0]
		pattern = f + '.cdg'
		rule = re.compile(re.escape(pattern), re.IGNORECASE)
		p = os.path.dirname(file_path)  # get the path, not the filename
		for n in os.listdir(p):
			if rule.match(n):
				return (file_path)
		if (1):
			# we didn't return, so always raise the exception: assert might work better?
			raise Exception("No matching .cdg file found for: " + file_path)

	def process_file(self, file_path):
		file_extension = os.path.splitext(file_path)[1]
		if (file_extension.casefold() == ".zip"):
			return self.handle_zipped_cdg(file_path)
		elif (file_extension.casefold() == ".mp3"):
			return self.handle_mp3_cdg(file_path)
		else:
			return file_path

	def play_file(self, file_path, params = []):
		try:
			file_path = self.process_file(file_path)
			if self.is_playing() or self.is_paused():
				logging.debug("VLC is currently playing, stopping track...")
				# must wait for VLC to quit or force kill, otherwise VLC http server will be borked
				try:
					self.stop()
					self.process.wait(0.2)
				except:
					self.process.kill()
			if self.platform == "windows":
				file_path = r"{}".format(file_path.replace('/', '\\'))
			command = self.cmd_base + params + [file_path]
			logging.debug("VLC Command: %s" % command)
			self.process = subprocess.Popen(command, shell = (self.platform == "windows"), stdin = subprocess.PIPE)
		except Exception as e:
			logging.error("Playing file failed: " + str(e))

	def play_file_transpose(self, file_path, semitones, extra_params = []):
		# --speex-resampler-quality=<integer [0 .. 10]>
		#  Resampling quality (0 = worst and fastest, 10 = best and slowest).

		# --src-converter-type={0 (Sinc function (best quality)), 1 (Sinc function (medium quality)),
		#      2 (Sinc function (fast)), 3 (Zero Order Hold (fastest)), 4 (Linear (fastest))}
		#  Sample rate converter type
		#  Different resampling algorithms are supported. The best one is slower, while the fast one exhibits
		#  low quality.

		if self.platform == "raspberry_pi":
			# pi sounds bad on hightest quality setting (CPU not sufficient)
			speex_quality = 10
			src_type = 1
		else:
			speex_quality = 10
			src_type = 0

		params = [
			"--audio-filter",
			"scaletempo_pitch",
			"--pitch-shift",
			"%s" % semitones,
			"--speex-resampler-quality",
			"%s" % speex_quality,
			"--src-converter-type",
			"%s" % src_type,
		]

		self.is_transposing = True
		logging.debug("Transposing file...")
		self.play_file(file_path, params + extra_params)

		# Prevent is_running() from returning False while we're transposing
		s = Timer(2.0, self.set_transposing_complete)
		s.start()

	def set_transposing_complete(self):
		self.is_transposing = False
		logging.debug("Transposing complete")

	def command(self, command):
		if self.is_running():
			url = self.http_command_endpoint + command
			request = requests.get(url, auth = ("", self.http_password))
			return request
		else:
			logging.error("No active VLC process. Could not run command: " + command)

	def pause(self):
		return self.command("pl_pause")

	def play(self):
		return self.command("pl_play")

	def get_val_xml(self, xml, key):
		posi = xml.find(f'<{key}>')
		if posi < 0:
			return None
		s = xml[posi+len(key)+2:]
		posi = s.find('<')
		if posi < 0:
			return None
		return s[:posi]

	def get_info_xml(self, xml=None):
		try:
			if xml is None:
				xml = self.get_status()
			return {key: float(self.get_val_xml(xml, key)) for key in ['position', 'length', 'volume', 'time', 'audiodelay']}
		except:
			return {}

	def seek(self, seek_sec):
		return self.command(f"seek&val={seek_sec}")

	def stop(self):
		try:
			return self.command("pl_stop")
		except:
			e = sys.exc_info()[0]
			logging.warn(f"Track stop: server may have shut down before http return code received: {e}")
			return

	def restart(self):
		logging.info(self.command("seek&val=0"))
		self.play()
		return self.command("seek&val=0")

	def vol_up(self):
		return self.command(f"volume&val=+{self.vol_increment}")

	def vol_down(self):
		return self.command(f"volume&val=-{self.vol_increment}")

	def vol_set(self, value):
		return self.command(f"volume&val={value}")

	def kill(self):
		try:
			self.process.kill()
		except (OSError, AttributeError) as e:
			print(e)
		return

	def is_running(self):
		return (self.process != None and self.process.poll() == None) or self.is_transposing

	def is_playing(self):
		if self.is_running():
			status = self.get_status()
			state = self.get_val_xml(status, 'state')
			return state == "playing"
		else:
			return False

	def is_paused(self):
		if self.is_running():
			status = self.get_status()
			state = self.get_val_xml(status, 'state')
			return state == "paused"
		else:
			return False

	def get_status(self):
		cur_time = time.time()
		if abs(cur_time-self.last_status_time)>1:
			try:
				url = self.http_endpoint
				self.last_status_text = requests.get(url, auth = ("", self.http_password)).text
				self.last_status_time = cur_time
				return self.last_status_text
			except: pass
		return self.last_status_text

	def run(self):
		try:
			while True:
				pass
		except KeyboardInterrupt:
			self.kill()

# if __name__ == "__main__":
#     k = VLCClient()
#     k.play_file("/path/to/file.mp4")
#     time.sleep(2)
#     k.pause()
#     k.vol_up()
#     k.vol_up()
#     time.sleep(2)
#     k.vol_down()
#     k.vol_down()
#     time.sleep(2)
#     k.play()
#     time.sleep(2)
#     k.stop()
