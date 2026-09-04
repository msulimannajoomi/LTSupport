# Point these at your deployed server's public IP/domain before distributing to clients.
API_BASE_URL = "https://9ntmvzk7-8000.inc1.devtunnels.ms"
RELAY_HOST = "0.tcp.in.ngrok.io"
RELAY_PORT = 25454

# Host mic -> viewer speaker. Raw PCM16 mono, no compression -- both sides must agree
# on this format since audio frames carry no header of their own.
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_BLOCK_SIZE = 1024  # samples per chunk sent (~64ms at 16kHz)

# Where session recordings are saved on the viewer's machine.
RECORDINGS_DIR_NAME = "Recordings"
