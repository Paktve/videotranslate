import os
import json
import time
import uuid
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
import urllib.request
import requests


PORT = 8080

ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY", "")

UPLOAD_WORKER_URL = os.environ.get(
    "UPLOAD_WORKER_URL",
    "https://round-boat-3282.paktve751.workers.dev/"
)

TEMP_DIR = "/tmp/videotranslate"


def run_command(command):
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise Exception(
            "Command failed:\n" + result.stderr[-4000:]
        )

    return result.stdout


def download_video(video_url, output_file):
    response = requests.get(
        video_url,
        headers={
            "User-Agent": "Mozilla/5.0"
        },
        stream=True,
        timeout=300
    )

    response.raise_for_status()

    with open(output_file, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)


def get_duration(video_file):
    output = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_file
    ])

    return float(output.strip())


def extract_audio(video_file, audio_file):
    run_command([
        "ffmpeg",
        "-y",
        "-i", video_file,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "mp3",
        audio_file
    ])


def assemblyai_transcribe(audio_file):
    headers = {
        "authorization": ASSEMBLYAI_API_KEY
    }

    with open(audio_file, "rb") as f:
        response = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers=headers,
            data=f,
            timeout=300
        )

    response.raise_for_status()

    audio_url = response.json()["upload_url"]

    response = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers={
            "authorization": ASSEMBLYAI_API_KEY,
            "content-type": "application/json"
        },
        json={
            "audio_url": audio_url
        },
        timeout=60
    )

    response.raise_for_status()

    transcript_id = response.json()["id"]

    while True:
        response = requests.get(
            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
            headers=headers,
            timeout=60
        )

        response.raise_for_status()

        data = response.json()
        status = data.get("status")

        if status == "completed":
            return data.get("text", "")

        if status == "error":
            raise Exception(
                "AssemblyAI error: " +
                str(data.get("error"))
            )

        time.sleep(3)


def translate_text(text):
    if not text.strip():
        return ""

    response = requests.get(
        "https://api.mymemory.translated.net/get",
        params={
            "q": text,
            "langpair": "en|ur"
        },
        timeout=60
    )

    response.raise_for_status()

    data = response.json()

    translated = data.get("responseData", {}).get("translatedText", "")

    if not translated:
        raise Exception("MyMemory translation failed")

    return translated


def create_tts(text, output_file):
    text_file = output_file + ".txt"

    with open(text_file, "w", encoding="utf-8") as f:
        f.write(text)

    run_command([
        "edge-tts",
        "--file",
        text_file,
        "--voice",
        "ur-PK-AsadNeural",
        "--write-media",
        output_file
    ])

    os.remove(text_file)


def create_final_video(video_file, voice_file, output_file, duration):
    run_command([
        "ffmpeg",
        "-y",
        "-i", video_file,
        "-i", voice_file,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-af", f"apad,atrim=0:{duration}",
        "-c:a", "aac",
        "-b:a", "128k",
        "-t", str(duration),
        "-shortest",
        output_file
    ])


def upload_final_video(video_file):
    filename = "translated_" + str(int(time.time())) + ".mp4"

    with open(video_file, "rb") as f:
        response = requests.post(
            UPLOAD_WORKER_URL +
            "?filename=" + filename,
            files={
                "videoFile": (
                    filename,
                    f,
                    "video/mp4"
                )
            },
            timeout=600
        )

    response.raise_for_status()

    return response.json()


def process_video(video_url):
    if not ASSEMBLYAI_API_KEY:
        raise Exception(
            "ASSEMBLYAI_API_KEY secret is not configured"
        )

    os.makedirs(TEMP_DIR, exist_ok=True)

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(TEMP_DIR, job_id)

    os.makedirs(job_dir, exist_ok=True)

    try:
        video_file = os.path.join(
            job_dir,
            "input.mp4"
        )

        audio_file = os.path.join(
            job_dir,
            "audio.mp3"
        )

        voice_file = os.path.join(
            job_dir,
            "urdu.mp3"
        )

        final_file = os.path.join(
            job_dir,
            "final.mp4"
        )

        english_file = os.path.join(
            job_dir,
            "transcript.txt"
        )

        urdu_file = os.path.join(
            job_dir,
            "urdu.txt"
        )

        print("Downloading video...")

        download_video(
            video_url,
            video_file
        )

        duration = get_duration(video_file)

        print("Extracting audio...")

        extract_audio(
            video_file,
            audio_file
        )

        print("Transcribing with AssemblyAI...")

        transcript = assemblyai_transcribe(
            audio_file
        )

        with open(
            english_file,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(transcript)

        print("Translating to Urdu...")

        urdu_text = translate_text(
            transcript
        )

        with open(
            urdu_file,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(urdu_text)

        print("Creating Urdu voice...")

        create_tts(
            urdu_text,
            voice_file
        )

        print("Creating final video...")

        create_final_video(
            video_file,
            voice_file,
            final_file,
            duration
        )

        print("Uploading final video to R2...")

        upload_result = upload_final_video(
            final_file
        )

        return {
            "success": True,
            "job_id": job_id,
            "message": "Video translation completed",
            "transcript": transcript,
            "translation": urdu_text,
            "result": upload_result
        }

    finally:
        shutil.rmtree(
            job_dir,
            ignore_errors=True
        )


class Handler(BaseHTTPRequestHandler):

    def send_json(self, status, data):
        body = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.end_headers()

        self.wfile.write(body)

    def do_GET(self):

        if self.path == "/":
            self.send_json(
                200,
                {
                    "success": True,
                    "service": "VideoTranslate AI",
                    "status": "running"
                }
            )
            return

        self.send_json(
            404,
            {
                "success": False,
                "message": "Not found"
            }
        )

    def do_POST(self):

        parsed = urlparse(self.path)

        if parsed.path != "/process":
            self.send_json(
                404,
                {
                    "success": False,
                    "message": "Use POST /process"
                }
            )
            return

        try:
            content_length = int(
                self.headers.get(
                    "Content-Length",
                    "0"
                )
            )

            body = self.rfile.read(
                content_length
            )

            data = json.loads(
                body.decode("utf-8")
            )

            video_url = data.get(
                "video_url"
            )

            if not video_url:
                self.send_json(
                    400,
                    {
                        "success": False,
                        "message":
                        "video_url is required"
                    }
                )
                return

            result = process_video(
                video_url
            )

            self.send_json(
                200,
                result
            )

        except Exception as error:

            print(
                "Processing error:",
                str(error)
            )

            self.send_json(
                500,
                {
                    "success": False,
                    "message":
                    "Video processing failed",
                    "error": str(error)
                }
            )


if __name__ == "__main__":

    print(
        f"VideoTranslate server starting on port {PORT}"
    )

    server = HTTPServer(
        ("0.0.0.0", PORT),
        Handler
    )

    server.serve_forever()
