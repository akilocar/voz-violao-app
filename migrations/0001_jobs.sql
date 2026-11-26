CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,            -- enviando | na_fila | processando | pronto | erro
  nome_arquivo TEXT,
  tipo TEXT,
  upload_id TEXT,                  -- id do upload multipart no R2
  intensidade REAL NOT NULL DEFAULT 0.6,
  estabilizar INTEGER NOT NULL DEFAULT 1,
  erro TEXT,
  criado_em INTEGER NOT NULL,
  atualizado_em INTEGER NOT NULL
);
