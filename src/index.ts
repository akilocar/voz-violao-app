import { Container, getContainer } from "@cloudflare/containers";

export interface Env {
  ASSETS: Fetcher;
  DB: D1Database;
  VIDEOS: R2Bucket;
  FILA: Queue<{ id: string }>;
  PROCESSOR: DurableObjectNamespace<Processor>;
}

/** Container com FFmpeg + Python que faz o processamento pesado. */
export class Processor extends Container<Env> {
  defaultPort = 8080;
  sleepAfter = "3m";
  enableInternet = false;
}

const MB = 1024 * 1024;
const TAMANHO_MAX = 500 * MB;
const TAMANHO_PARTE = 50 * MB; // abaixo do limite de corpo de requisição do Worker
const TIPOS_OK = /^video\//;

const json = (dados: unknown, status = 200) =>
  new Response(JSON.stringify(dados), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
const erro = (msg: string, status = 400) => json({ erro: msg }, status);

const chaveOriginal = (id: string) => `originais/${id}`;
const chaveResultado = (id: string) => `resultados/${id}.mp4`;

type Job = {
  id: string;
  status: string;
  nome_arquivo: string | null;
  tipo: string | null;
  upload_id: string | null;
  intensidade: number;
  estabilizar: number;
  erro: string | null;
};

async function lerJob(env: Env, id: string) {
  return env.DB.prepare("SELECT * FROM jobs WHERE id = ?").bind(id).first<Job>();
}

async function atualizar(env: Env, id: string, campos: Partial<Job>) {
  const chaves = Object.keys(campos);
  const sets = chaves.map((k) => `${k} = ?`).join(", ");
  await env.DB.prepare(`UPDATE jobs SET ${sets}, atualizado_em = ? WHERE id = ?`)
    .bind(...chaves.map((k) => (campos as Record<string, unknown>)[k]), Date.now(), id)
    .run();
}

async function servirVideo(env: Env, req: Request, chave: string, nome: string) {
  const obj = await env.VIDEOS.get(chave, { range: req.headers, onlyIf: req.headers });
  if (!obj) return erro("arquivo não encontrado", 404);
  const headers = new Headers();
  obj.writeHttpMetadata(headers);
  headers.set("etag", obj.httpEtag);
  headers.set("Accept-Ranges", "bytes");
  headers.set("Content-Disposition", `inline; filename="${nome}"`);
  if (!("body" in obj)) return new Response(null, { status: 304, headers });
  const r = obj.range as { offset?: number; length?: number } | undefined;
  if (req.headers.has("range") && r && r.offset !== undefined) {
    const fim = r.offset + (r.length ?? obj.size - r.offset) - 1;
    headers.set("Content-Range", `bytes ${r.offset}-${fim}/${obj.size}`);
    return new Response(obj.body, { status: 206, headers });
  }
  return new Response(obj.body, { headers });
}

async function api(req: Request, env: Env, url: URL): Promise<Response> {
  const partes = url.pathname.split("/").filter(Boolean); // ["api","jobs",id,...]
  const [, recurso, id, acao, numero] = partes;
  if (recurso !== "jobs") return erro("rota desconhecida", 404);

  // Cria o pedido e inicia o upload em partes no R2.
  if (!id && req.method === "POST") {
    const corpo = (await req.json().catch(() => ({}))) as {
      nome?: string; tipo?: string; tamanho?: number;
    };
    if (!corpo.tamanho || corpo.tamanho > TAMANHO_MAX)
      return erro(`O vídeo precisa ter até ${TAMANHO_MAX / MB} MB.`);
    if (!corpo.tipo || !TIPOS_OK.test(corpo.tipo)) return erro("Envie um arquivo de vídeo.");
    const novoId = crypto.randomUUID();
    const up = await env.VIDEOS.createMultipartUpload(chaveOriginal(novoId), {
      httpMetadata: { contentType: corpo.tipo },
    });
    const agora = Date.now();
    await env.DB.prepare(
      `INSERT INTO jobs (id, status, nome_arquivo, tipo, upload_id, criado_em, atualizado_em)
       VALUES (?, 'enviando', ?, ?, ?, ?, ?)`,
    ).bind(novoId, (corpo.nome ?? "video").slice(0, 200), corpo.tipo, up.uploadId, agora, agora).run();
    return json({ id: novoId, tamanhoParte: TAMANHO_PARTE }, 201);
  }

  if (!id) return erro("rota desconhecida", 404);
  const job = await lerJob(env, id);
  if (!job) return erro("pedido não encontrado", 404);

  if (!acao && req.method === "GET") {
    return json({ id: job.id, status: job.status, erro: job.erro, nome: job.nome_arquivo });
  }

  if (acao === "partes" && req.method === "PUT") {
    if (job.status !== "enviando" || !job.upload_id) return erro("upload já encerrado", 409);
    const n = Number(numero);
    if (!Number.isInteger(n) || n < 1 || n > 10000 || !req.body) return erro("parte inválida");
    const up = env.VIDEOS.resumeMultipartUpload(chaveOriginal(id), job.upload_id);
    const parte = await up.uploadPart(n, req.body);
    return json({ partNumber: parte.partNumber, etag: parte.etag });
  }

  if (acao === "concluir" && req.method === "POST") {
    if (job.status !== "enviando" || !job.upload_id) return erro("upload já encerrado", 409);
    const corpo = (await req.json().catch(() => ({}))) as {
      partes?: R2UploadedPart[]; intensidade?: number; estabilizar?: boolean;
    };
    if (!corpo.partes?.length) return erro("nenhuma parte enviada");
    const up = env.VIDEOS.resumeMultipartUpload(chaveOriginal(id), job.upload_id);
    await up.complete(corpo.partes);
    const intensidade = Math.min(1, Math.max(0, Number(corpo.intensidade ?? 0.6)));
    await atualizar(env, id, {
      status: "na_fila",
      upload_id: null,
      intensidade,
      estabilizar: corpo.estabilizar === false ? 0 : 1,
    });
    await env.FILA.send({ id });
    return json({ id, status: "na_fila" });
  }

  if (acao === "original" && req.method === "GET") {
    return servirVideo(env, req, chaveOriginal(id), job.nome_arquivo ?? "original");
  }
  if (acao === "resultado" && req.method === "GET") {
    if (job.status !== "pronto") return erro("ainda não está pronto", 409);
    const base = (job.nome_arquivo ?? "video").replace(/\.[^.]+$/, "");
    return servirVideo(env, req, chaveResultado(id), `${base}-melhorado.mp4`);
  }

  return erro("rota desconhecida", 404);
}

async function processar(env: Env, id: string) {
  const job = await lerJob(env, id);
  if (!job || job.status === "pronto") return;
  await atualizar(env, id, { status: "processando", erro: null });

  const original = await env.VIDEOS.get(chaveOriginal(id));
  if (!original) throw new Error("vídeo original não encontrado");

  // O container precisa do Content-Length; FixedLengthStream garante isso.
  const entrada = new FixedLengthStream(original.size);
  original.body.pipeTo(entrada.writable);

  const qs = new URLSearchParams({
    intensidade: String(job.intensidade),
    estabilizar: job.estabilizar ? "1" : "0",
  });
  const container = getContainer(env.PROCESSOR, id);
  const resp = await container.fetch(
    new Request(`http://processor/process?${qs}`, { method: "POST", body: entrada.readable }),
  );
  if (!resp.ok || !resp.body) {
    throw new Error(`processamento falhou (${resp.status}): ${(await resp.text()).slice(-500)}`);
  }
  const tamanho = Number(resp.headers.get("Content-Length"));
  const saida = new FixedLengthStream(tamanho);
  resp.body.pipeTo(saida.writable);
  await env.VIDEOS.put(chaveResultado(id), saida.readable, {
    httpMetadata: { contentType: "video/mp4" },
  });
  await atualizar(env, id, { status: "pronto" });
}

export default {
  async fetch(req, env): Promise<Response> {
    const url = new URL(req.url);
    if (url.pathname.startsWith("/api/")) {
      try {
        return await api(req, env, url);
      } catch (e) {
        console.error(e);
        return erro("Erro interno. Tente de novo.", 500);
      }
    }
    return env.ASSETS.fetch(req);
  },

  async queue(lote, env): Promise<void> {
    for (const msg of lote.messages) {
      try {
        await processar(env, msg.body.id);
        msg.ack();
      } catch (e) {
        console.error("falha no pedido", msg.body.id, e);
        if (msg.attempts >= 2) {
          await atualizar(env, msg.body.id, {
            status: "erro",
            erro: "Não conseguimos processar este vídeo. Tente outro arquivo.",
          });
          msg.ack();
        } else {
          msg.retry({ delaySeconds: 30 });
        }
      }
    }
  },
} satisfies ExportedHandler<Env, { id: string }>;
