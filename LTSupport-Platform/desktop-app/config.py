# Point these at your deployed server's public IP/domain before distributing to clients.
API_BASE_URL = "http://136.116.38.224:8000"
RELAY_HOST = "136.116.38.224"
RELAY_PORT = 7000

# Host mic -> viewer speaker. Raw PCM16 mono, no compression -- both sides must agree
# on this format since audio frames carry no header of their own.
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_BLOCK_SIZE = 1024  # samples per chunk sent (~64ms at 16kHz)

# Where session recordings are saved on the viewer's machine.
RECORDINGS_DIR_NAME = "Recordings"
