import { runPipeline, type PipelineEvent } from "@/lib/pipeline";

export const runtime = "nodejs";
// Universal first-run crawls are long; raise as far as the plan allows. The
// 7-day GSC cache + 90-day metadata cache keep subsequent runs well inside this.
export const maxDuration = 300;

export async function POST(req: Request) {
  const { domains, topic, startDate, endDate } = await req.json();

  if (!Array.isArray(domains) || !domains.length) {
    return new Response(JSON.stringify({ error: "Select at least one domain." }), { status: 400 });
  }
  if (!topic || !String(topic).trim()) {
    return new Response(JSON.stringify({ error: "Enter a topic before running." }), { status: 400 });
  }

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      const emit = (e: PipelineEvent) => controller.enqueue(encoder.encode(JSON.stringify(e) + "\n"));
      try {
        await runPipeline({ domains, topic: String(topic).trim(), startDate, endDate }, emit);
      } catch (exc) {
        emit({ type: "error", code: "hf_error", message: exc instanceof Error ? exc.message : "Pipeline failed." });
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "application/x-ndjson; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}
