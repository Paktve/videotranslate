import { Container, getContainer } from "@cloudflare/containers";
import { Hono } from "hono";

export class MyContainer extends Container<Env> {
  defaultPort = 8080;

  // Video processing کے دوران container جلدی sleep نہ ہو
  sleepAfter = "30m";

  // Container کو AssemblyAI, Google Translate وغیرہ سے internet چاہیے
  enableInternet = true;

  override onStart() {
    console.log("VideoTranslate container started");
  }

  override onStop() {
    console.log("VideoTranslate container stopped");
  }

  override onError(error: unknown) {
    console.log("VideoTranslate container error:", error);
  }
}

const app = new Hono<{
  Bindings: Env;
}>();


// --------------------------------------------------
// HOME / HEALTH CHECK
// --------------------------------------------------

app.get("/", (c) => {
  return c.json({
    success: true,
    service: "VideoTranslate AI",
    status: "online"
  });
});


// --------------------------------------------------
// VIDEO PROCESSING
// --------------------------------------------------

app.post("/process", async (c) => {

  try {

    const container = getContainer(
      c.env.MY_CONTAINER,
      "video-processor"
    );

    // AssemblyAI API key کو Worker Secret سے
    // Container کے environment variable میں بھیجیں
    await container.startAndWaitForPorts({
      startOptions: {
        envVars: {
          ASSEMBLYAI_API_KEY:
            c.env.ASSEMBLYAI_API_KEY
        }
      }
    });

    // اصل request Python Container کو forward کریں
    const response = await container.fetch(c.req.raw);

    // CORS headers واپس لگائیں
    const headers = new Headers(response.headers);

    headers.set(
      "Access-Control-Allow-Origin",
      "*"
    );

    headers.set(
      "Access-Control-Allow-Methods",
      "POST, OPTIONS"
    );

    headers.set(
      "Access-Control-Allow-Headers",
      "Content-Type"
    );

    return new Response(
      response.body,
      {
        status: response.status,
        headers: headers
      }
    );

  } catch (error) {

    return c.json(
      {
        success: false,
        message: "Container processing failed",
        error: String(error)
      },
      500
    );
  }

});


// --------------------------------------------------
// CORS OPTIONS
// --------------------------------------------------

app.options("*", (c) => {

  return new Response(null, {
    status: 204,
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type"
    }
  });

});


export default app;
