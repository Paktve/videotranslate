import os
import json
import time
import uuid
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
import requests
import traceback


PORT = 8080

ASSEMBLYAI_API_KEY = os.environ.get(
    "ASSEMBLYAI_API_KEY",
    ""
)

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
            "Command failed:\n" +
            result.stderr[-4000:]
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
        for chunk in response.iter_content(
            chunk_size=1024 * 1024
        ):
            if chunk:
                f.write(chunk)


def get_duration(video_file):
    output = run_command([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_file
    ])

    return float(output.strip())


def extract_audio(video_file, audio_file):
    run_command([
        "ffmpeg",
        "-y",
        "-i",
        video_file,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "mp3",
        audio_file
    ])


def get_mean_volume(audio_file):
    result = subprocess.run(
        [
            "ffmpeg",
            "-i",
            audio_file,
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-"
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    output = result.stderr

    for line in output.splitlines():

        if "mean_volume:" in line:

            value = line.split(
                "mean_volume:"
            )[1].strip()

            value = value.replace(
                " dB",
                ""
            ).strip()

            try:
                return float(value)
            except:
                pass

    return None


def adjust_audio_volume(
    input_file,
    output_file,
    gain_db
):
    run_command([
        "ffmpeg",
        "-y",
        "-i",
        input_file,
        "-af",
        f"volume={gain_db}dB",
        "-c:a",
        "mp3",
        "-b:a",
        "192k",
        output_file
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

            words = data.get(
                "words",
                []
            )

            segments = []

            current_words = []

            for word in words:

                current_words.append(word)

                word_text = word.get(
                    "text",
                    ""
                )

                end_sentence = (
                    word_text.endswith(".")
                    or word_text.endswith("?")
                    or word_text.endswith("!")
                )

                # Smaller segments help keep
                # Urdu speech closer to the
                # original timing.
                max_words = (
                    len(current_words) >= 12
                )

                if end_sentence or max_words:

                    start = (
                        current_words[0]["start"]
                        / 1000
                    )

                    end = (
                        current_words[-1]["end"]
                        / 1000
                    )

                    text = " ".join(
                        w.get("text", "")
                        for w in current_words
                    ).strip()

                    if text:

                        segments.append({
                            "start": start,
                            "end": end,
                            "text": text
                        })

                    current_words = []

            if current_words:

                start = (
                    current_words[0]["start"]
                    / 1000
                )

                end = (
                    current_words[-1]["end"]
                    / 1000
                )

                text = " ".join(
                    w.get("text", "")
                    for w in current_words
                ).strip()

                if text:

                    segments.append({
                        "start": start,
                        "end": end,
                        "text": text
                    })

            if not segments:

                raise Exception(
                    "AssemblyAI returned no speech segments"
                )

            return segments

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

    translated = data.get(
        "responseData",
        {}
    ).get(
        "translatedText",
        ""
    )

    if not translated:

        raise Exception(
            "MyMemory translation failed"
        )

    return translated


def create_tts(
    text,
    output_file
):

    text_file = output_file + ".txt"

    with open(
        text_file,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(text)

    voices = [
        "ur-PK-AsadNeural",
        "ur-PK-UzmaNeural"
    ]

    last_error = None

    try:

        for voice in voices:

            for attempt in range(1, 4):

                try:

                    print(
                        "TTS voice:",
                        voice,
                        "| attempt:",
                        attempt
                    )

                    run_command([
                        "edge-tts",
                        "--file",
                        text_file,
                        "--voice",
                        voice,
                        "--rate",
                        "+0%",
                        "--write-media",
                        output_file
                    ])

                    if (
                        os.path.exists(output_file)
                        and os.path.getsize(output_file) > 1000
                    ):
                        return

                except Exception as error:

                    last_error = error

                    print(
                        "TTS failed:",
                        str(error)
                    )

                    if os.path.exists(
                        output_file
                    ):
                        os.remove(
                            output_file
                        )

                    time.sleep(2)

    finally:

        if os.path.exists(text_file):
            os.remove(text_file)

    raise Exception(
        "Urdu TTS failed after all attempts: "
        + str(last_error)
    )


def get_audio_duration(
    audio_file
):

    output = run_command([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        audio_file
    ])

    return float(
        output.strip()
    )


def slightly_adjust_speed(
    input_file,
    output_file,
    speed
):

    run_command([
        "ffmpeg",
        "-y",
        "-i",
        input_file,
        "-filter:a",
        f"atempo={speed}",
        "-vn",
        output_file
    ])


def create_dubbed_audio(
    segments,
    output_file,
    job_dir,
    total_duration,
    original_audio_file
):

    audio_files = []

    urdu_parts = []

    for index, segment in enumerate(
        segments
    ):

        english_text = segment["text"]

        print(
            "Translating segment",
            index + 1,
            "/",
            len(segments)
        )

        urdu_text = translate_text(
            english_text
        )

        if not urdu_text.strip():
            continue

        urdu_parts.append(
            urdu_text
        )

        segment_file = os.path.join(
            job_dir,
            f"segment_{index}.mp3"
        )

        print(
            "Creating Urdu voice",
            index + 1,
            "/",
            len(segments)
        )

        create_tts(
            urdu_text,
            segment_file
        )

        original_start = float(
            segment["start"]
        )

        original_end = float(
            segment["end"]
        )

        target_duration = (
            original_end -
            original_start
        )

        voice_duration = get_audio_duration(
            segment_file
        )

        print(
            "Original:",
            round(target_duration, 2),
            "sec | Urdu:",
            round(voice_duration, 2),
            "sec"
        )

        # Only a small speed correction.
        # Never use the old 1.35x + 1.35x method.
        if (
            target_duration > 0
            and voice_duration >
            target_duration * 1.10
        ):

            speed = (
                voice_duration /
                target_duration
            )

            # Maximum correction = 1.10x
            speed = min(
                speed,
                1.10
            )

            adjusted_file = os.path.join(
                job_dir,
                f"segment_{index}_adjusted.mp3"
            )

            slightly_adjust_speed(
                segment_file,
                adjusted_file,
                speed
            )

            os.remove(
                segment_file
            )

            segment_file = adjusted_file

        audio_files.append({
            "file": segment_file,
            "start": original_start
        })

    if not audio_files:

        raise Exception(
            "No Urdu audio segments were created"
        )

    inputs = []

    filters = []

    for index, item in enumerate(
        audio_files
    ):

        inputs.extend([
            "-i",
            item["file"]
        ])

        delay_ms = int(
            item["start"] * 1000
        )

        filters.append(
            f"[{index}:a]"
            f"adelay={delay_ms}|{delay_ms}"
            f"[a{index}]"
        )

    mix_inputs = "".join(
        f"[a{i}]"
        for i in range(
            len(audio_files)
        )
    )

    filter_complex = ";".join(
        filters
    )

    filter_complex += (
        f";{mix_inputs}"
        f"amix="
        f"inputs={len(audio_files)}:"
        f"duration=longest:"
        f"dropout_transition=0"
        f",atrim=0:{total_duration}"
        f"[mixed]"
    )

    raw_output = os.path.join(
        job_dir,
        "urdu_raw.mp3"
    )

    command = [
        "ffmpeg",
        "-y"
    ]

    command.extend(
        inputs
    )

    command.extend([
        "-filter_complex",
        filter_complex,
        "-map",
        "[mixed]",
        "-c:a",
        "mp3",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-t",
        str(total_duration),
        raw_output
    ])

    run_command(
        command
    )

    # Match generated voice loudness
    # approximately to the original video.
    original_volume = get_mean_volume(
        original_audio_file
    )

    generated_volume = get_mean_volume(
        raw_output
    )

    if (
        original_volume is not None
        and generated_volume is not None
    ):

        gain = (
            original_volume -
            generated_volume
        )

        # Prevent extreme volume changes.
        gain = max(
            -12.0,
            min(
                gain,
                12.0
            )
        )

        print(
            "Original mean volume:",
            round(
                original_volume,
                2
            ),
            "dB"
        )

        print(
            "Generated mean volume:",
            round(
                generated_volume,
                2
            ),
            "dB"
        )

        print(
            "Applying volume gain:",
            round(
                gain,
                2
            ),
            "dB"
        )

        adjust_audio_volume(
            raw_output,
            output_file,
            gain
        )

        os.remove(
            raw_output
        )

    else:

        shutil.move(
            raw_output,
            output_file
        )

    return "\n".join(
        urdu_parts
    )


def create_final_video(
    video_file,
    voice_file,
    output_file,
    duration
):

    run_command([
        "ffmpeg",
        "-y",
        "-i",
        video_file,
        "-i",
        voice_file,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-t",
        str(duration),
        output_file
    ])


def upload_final_video(
    video_file
):

    filename = (
        "translated_" +
        str(int(time.time())) +
        ".mp4"
    )

    with open(
        video_file,
        "rb"
    ) as f:

        response = requests.post(
            UPLOAD_WORKER_URL +
            "?filename=" +
            filename,
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


def process_video(
    video_url
):

    if not ASSEMBLYAI_API_KEY:

        raise Exception(
            "ASSEMBLYAI_API_KEY secret is not configured"
        )

    os.makedirs(
        TEMP_DIR,
        exist_ok=True
    )

    job_id = str(
        uuid.uuid4()
    )

    job_dir = os.path.join(
        TEMP_DIR,
        job_id
    )

    os.makedirs(
        job_dir,
        exist_ok=True
    )

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

        print(
            "Downloading video..."
        )

        download_video(
            video_url,
            video_file
        )

        duration = get_duration(
            video_file
        )

        print(
            "Extracting original audio..."
        )

        extract_audio(
            video_file,
            audio_file
        )

        print(
            "Transcribing with AssemblyAI..."
        )

        segments = assemblyai_transcribe(
            audio_file
        )

        english_text = "\n".join(
            segment["text"]
            for segment in segments
        )

        with open(
            english_file,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                english_text
            )

        print(
            "Creating Urdu dubbed audio..."
        )

        urdu_text = create_dubbed_audio(
            segments,
            voice_file,
            job_dir,
            duration,
            audio_file
        )

        with open(
            urdu_file,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                urdu_text
            )

        print(
            "Creating final video..."
        )

        create_final_video(
            video_file,
            voice_file,
            final_file,
            duration
        )

        print(
            "Uploading final video to R2..."
        )

        upload_result = upload_final_video(
            final_file
        )

        return {
            "success": True,
            "job_id": job_id,
            "message":
                "Video translation completed",
            "transcript":
                english_text,
            "translation":
                urdu_text,
            "result":
                upload_result
        }

    finally:

        shutil.rmtree(
            job_dir,
            ignore_errors=True
        )


class Handler(
    BaseHTTPRequestHandler
):

    def send_json(
        self,
        status,
        data
    ):

        body = json.dumps(
            data,
            ensure_ascii=False
        ).encode(
            "utf-8"
        )

        self.send_response(
            status
        )

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.end_headers()

        self.wfile.write(
            body
        )

    def do_GET(
        self
    ):

        if self.path == "/":

            self.send_json(
                200,
                {
                    "success": True,
                    "service":
                        "VideoTranslate AI",
                    "status":
                        "running"
                }
            )

            return

        self.send_json(
            404,
            {
                "success": False,
                "message":
                    "Not found"
            }
        )

    def do_POST(
        self
    ):

        parsed = urlparse(
            self.path
        )

        if parsed.path != "/process":

            self.send_json(
                404,
                {
                    "success": False,
                    "message":
                        "Use POST /process"
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
                body.decode(
                    "utf-8"
                )
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
            traceback.print_exc()

            self.send_json(
                500,
                {
                    "success": False,
                    "message":
                        "Video processing failed",
                    "error":
                        str(error)
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
