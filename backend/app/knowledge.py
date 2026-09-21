"""Local document service: ingest, chunk storage, retrieval, graph, OCR and embedding.

Skill routing/execution has moved to backend.app.capabilities (SkillRuntime/ToolRuntime).
Only the vision tool uses a remote model, exclusively through the budget gateway.
Local retrieval, parsing, OCR and embedding never send document contents elsewhere.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from .capabilities.services import documents as document_service
from .capabilities.services.embeddings import EMBEDDING_MODEL
from .storage import DATA_DIR, store

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_CHARS = document_service.MAX_EXTRACTED_CHARS
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".pdf", ".docx"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _tokens(text: str) -> set[str]:
    return document_service.tokenize(text)


def _decode(content: bytes) -> str:
    return document_service.decode_text(content)


def _split(text: str, location: str, size: int = 900) -> list[tuple[str, str]]:
    return document_service.split_parts(text, location, size)


def _extract(suffix: str, content: bytes) -> list[tuple[str, str]]:
    return document_service.extract_parts(suffix, content)


class Knowledge:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or DATA_DIR
        self._embedding: Any = None
        self._embedding_lock = threading.Lock()
        self._embedding_state = "not_loaded"
        self._embedding_error: str | None = None
        self._vector_cache: dict[str, tuple[str, Any]] = {}
        self._ocr: Any = None
        self._ocr_lock = threading.Lock()

    def seed(self) -> None:
        from .seeds import seed_all

        seed_all()

    def embedding_status(self) -> dict:
        return {
            "status": self._embedding_state,
            "model": EMBEDDING_MODEL,
            "device": "CPU",
            "error": self._embedding_error,
            "note": "首次调用时下载公开模型到本机；仅模型下载联网，文档嵌入始终在本地运行",
        }

    def _file_path(self, document: dict) -> Path:
        document_id = document.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", document_id):
            raise ValueError("资料标识无效")
        suffix = Path(document["name"]).suffix.lower()
        root = (self.data_dir / "uploads").resolve()
        path = (root / f"{document_id}{suffix}").resolve()
        if path.parent != root:
            raise ValueError("资料路径无效")
        return path

    def document_path(self, document_id: str) -> Path:
        document = store.get("documents", document_id)
        if not document:
            raise ValueError("资料不存在")
        path = self._file_path(document)
        if not path.is_file():
            raise ValueError("资料原文件不存在")
        return path

    def ingest(
        self,
        filename: str,
        content: bytes,
        project_id: str,
        visibility: str,
        temporary: bool = False,
    ) -> dict:
        if not filename or any(character in filename for character in ("/", "\\", ":", "\x00")):
            raise ValueError("文件名不得包含路径")
        if filename in {".", ".."} or len(filename) > 200:
            raise ValueError("文件名无效")
        if visibility not in {"external", "local"}:
            raise ValueError("资料可见性必须为 external 或 local")
        if not project_id or not store.get("projects", project_id):
            raise ValueError("请选择存在的项目")
        if not content or len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("文件不能为空且不能超过 25 MB")
        suffix = Path(filename).suffix.lower()
        if suffix not in TEXT_SUFFIXES | IMAGE_SUFFIXES:
            raise ValueError("不支持该文件类型，可上传 PDF、DOCX、TXT、MD、CSV、JSON 或图片")
        if suffix in IMAGE_SUFFIXES:
            from PIL import Image

            with Image.open(io.BytesIO(content)) as picture:
                if picture.width * picture.height > 25_000_000:
                    raise ValueError("图片超过 2500 万像素限制")
                picture.verify()
            parts = []
        else:
            try:
                parts = _extract(suffix, content)
            except (ValueError, RuntimeError):
                raise
            except Exception as error:
                raise ValueError(f"文档解析失败（{type(error).__name__}），请检查文件格式") from error
        document = {
            "id": "doc_" + uuid.uuid4().hex,
            "name": filename,
            "project_id": project_id,
            "visibility": visibility,
            "status": "ready"
            if parts
            else "needs_ocr"
            if suffix in IMAGE_SUFFIXES or suffix == ".pdf"
            else "empty",
            "chunk_count": len(parts),
            "size": len(content),
            "kind": suffix[1:],
            "sha256": hashlib.sha256(content).hexdigest(),
            "synthetic": False,
            "temporary": bool(temporary),
        }
        path = self._file_path(document)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        try:
            saved = store.save("documents", document)
            for index, (location, text) in enumerate(parts):
                store.save(
                    "chunks",
                    {
                        "id": f"{document['id']}_c{index}",
                        "document_id": document["id"],
                        "document_name": filename,
                        "project_id": project_id,
                        "visibility": visibility,
                        "location": location,
                        "text": text,
                        "index": index,
                    },
                )
        except Exception:
            for chunk in store.list("chunks"):
                if chunk.get("document_id") == document["id"]:
                    store.delete("chunks", chunk["id"])
            store.delete("documents", document["id"])
            path.unlink(missing_ok=True)
            raise
        store.audit(
            "document.ingest",
            document["id"],
            {"name": filename, "visibility": visibility, "chunks": len(parts)},
        )
        return saved

    def chunks(self, document_id: str) -> list[dict]:
        document = store.get("documents", document_id)
        if not document:
            raise ValueError("资料不存在")
        return sorted(
            [
                {**chunk, "visibility": document["visibility"]}
                for chunk in store.list("chunks")
                if chunk.get("document_id") == document_id
            ],
            key=lambda chunk: chunk.get("index", 0),
        )

    def remove(self, document_id: str) -> bool:
        document = store.get("documents", document_id)
        if not document:
            return False
        for chunk in self.chunks(document_id):
            store.delete("chunks", chunk["id"])
            self._vector_cache.pop(chunk["id"], None)
        for edge in store.list("graph_edges"):
            if edge.get("document_id") == document_id:
                store.delete("graph_edges", edge["id"])
        self._file_path(document).unlink(missing_ok=True)
        removed = store.delete("documents", document_id)
        store.audit("document.delete", document_id, {"name": document["name"]})
        return removed

    def _candidates(self, project_id: str | None, document_ids: list[str] | None) -> list[dict]:
        docs = {doc["id"]: doc for doc in store.list("documents")}
        selected = set(document_ids) if document_ids is not None else None
        return [
            {**chunk, "visibility": docs[chunk["document_id"]]["visibility"]}
            for chunk in store.list("chunks")
            if chunk.get("document_id") in docs
            and (not project_id or docs[chunk["document_id"]].get("project_id") == project_id)
            and (selected is None or chunk["document_id"] in selected)
            # Run-scoped uploads never leak into library-wide retrieval.
            and (selected is not None or not docs[chunk["document_id"]].get("temporary"))
        ]

    @staticmethod
    def _evidence(chunk: dict, score: float) -> dict:
        return document_service.build_evidence(chunk, score)

    def _get_embedding(self) -> Any:
        if self._embedding is not None:
            return self._embedding
        with self._embedding_lock:
            if self._embedding is not None:
                return self._embedding
            self._embedding_state = "loading"
            self._embedding_error = None
            try:
                from fastembed import TextEmbedding

                cache = self.data_dir / "models" / "fastembed"
                cache.mkdir(parents=True, exist_ok=True)
                self._embedding = TextEmbedding(
                    model_name=EMBEDDING_MODEL,
                    cache_dir=str(cache),
                    threads=2,
                    providers=["CPUExecutionProvider"],
                )
                self._embedding_state = "ready"
            except Exception as error:
                self._embedding_state = "unavailable"
                self._embedding_error = (
                    f"中文语义模型准备失败（{type(error).__name__}），可联网准备模型后重试"
                )
                raise RuntimeError(self._embedding_error) from error
        return self._embedding

    def search(
        self,
        query: str,
        project_id: str | None = None,
        document_ids: list[str] | None = None,
        limit: int = 6,
        semantic: bool = False,
    ) -> list[dict]:
        query = str(query or "").strip()
        if not query:
            return []
        if len(query) > 20_000:
            raise ValueError("检索问题过长")
        limit = min(max(int(limit), 1), 50)
        candidates = self._candidates(project_id, document_ids)
        if not candidates:
            return []
        ranked: list[tuple[float, dict]] = []
        if semantic:
            import numpy as np

            model = self._get_embedding()
            missing = [
                chunk
                for chunk in candidates
                if self._vector_cache.get(chunk["id"], (None,))[0]
                != hashlib.sha256(chunk["text"].encode()).hexdigest()
            ]
            if missing:
                for chunk, vector in zip(
                    missing, model.embed([chunk["text"] for chunk in missing], batch_size=16)
                ):
                    self._vector_cache[chunk["id"]] = (
                        hashlib.sha256(chunk["text"].encode()).hexdigest(),
                        vector,
                    )
            question = next(iter(model.query_embed(query)))
            for chunk in candidates:
                vector = self._vector_cache[chunk["id"]][1]
                denominator = float(np.linalg.norm(question) * np.linalg.norm(vector))
                score = float(np.dot(question, vector) / denominator) if denominator else 0.0
                ranked.append((score, chunk))
        else:
            query_tokens = _tokens(query)
            if not query_tokens:
                return []
            token_sets = [_tokens(chunk["text"] + " " + chunk["document_name"]) for chunk in candidates]
            frequencies = {token: sum(token in terms for terms in token_sets) for token in query_tokens}
            for chunk, terms in zip(candidates, token_sets):
                matched = query_tokens & terms
                if not matched:
                    continue
                score = sum(math.log(1 + len(candidates) / (1 + frequencies[token])) for token in matched)
                score /= max(1, len(query_tokens))
                if query.lower() in chunk["text"].lower():
                    score += 1.0
                ranked.append((score, chunk))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        return [self._evidence(chunk, score) for score, chunk in ranked[:limit]]

    def graph(self, project_id: str | None = None) -> dict:
        nodes = [
            node
            for node in store.list("graph_nodes")
            if not project_id or node.get("project_id") == project_id
        ]
        ids = {node["id"] for node in nodes}
        edges = [
            edge
            for edge in store.list("graph_edges")
            if edge.get("source") in ids and edge.get("target") in ids
        ]
        return {"nodes": nodes, "edges": edges}

    def _ocr_document(self, document_id: str) -> dict:
        document = store.get("documents", document_id)
        if not document:
            raise ValueError("资料不存在")
        if "." + document["kind"] not in IMAGE_SUFFIXES:
            raise ValueError("OCR 当前支持 PNG、JPEG、WEBP、BMP 图片；扫描 PDF 请先导出图片")
        try:
            from rapidocr_onnxruntime import RapidOCR

            with self._ocr_lock:
                if self._ocr is None:
                    self._ocr = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
                result, elapsed = self._ocr(str(self.document_path(document_id)))
        except ImportError as error:
            raise RuntimeError("RapidOCR 依赖未安装，OCR 不可用") from error
        except Exception as error:
            raise RuntimeError(f"OCR 识别失败（{type(error).__name__}）") from error
        for existing in self.chunks(document_id):
            if existing.get("source_skill") == "ocr":
                store.delete("chunks", existing["id"])
        evidence = []
        for number, record in enumerate(result or []):
            box, text, confidence = record
            chunk = store.save(
                "chunks",
                {
                    "id": f"{document_id}_ocr{number}",
                    "document_id": document_id,
                    "document_name": document["name"],
                    "project_id": document["project_id"],
                    "visibility": document["visibility"],
                    "location": f"图片第 {number + 1} 个文字区域",
                    "text": text,
                    "index": number,
                    "bbox": box,
                    "confidence": float(confidence),
                    "source_skill": "ocr",
                },
            )
            evidence.append(self._evidence(chunk, float(confidence)))
        store.save(
            "documents",
            {
                **document,
                "chunk_count": len(self.chunks(document_id)),
                "status": "ready" if evidence else "empty",
            },
        )
        store.audit("skill.ocr", document_id, {"recognized_regions": len(evidence)})
        return {
            "skill_id": "ocr",
            "status": "completed",
            "evidence": evidence,
            "text": "\n".join(item["text"] for item in evidence),
            "region_count": len(evidence),
            "elapsed": elapsed,
        }

knowledge = Knowledge()
