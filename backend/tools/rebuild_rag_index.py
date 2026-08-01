"""Rebuild the bundled Chroma vector store from PDF and TXT source documents."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete the existing vector store before rebuilding it.",
    )
    args = parser.parse_args()

    settings = get_settings().rag
    root = Path(__file__).resolve().parents[1]
    sources = (root / settings.sources_path).resolve()
    destination = (root / settings.vector_store_path).resolve()

    if destination.exists():
        if not args.force:
            raise SystemExit(
                f"Vector store already exists at {destination}. Re-run with --force to replace it."
            )
        shutil.rmtree(destination)

    from langchain_chroma import Chroma
    from langchain_community.document_loaders import (
        DirectoryLoader,
        PyPDFDirectoryLoader,
        TextLoader,
    )
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    documents = []
    if sources.is_dir():
        documents.extend(PyPDFDirectoryLoader(str(sources)).load())
        documents.extend(
            DirectoryLoader(
                str(sources),
                glob="**/*.txt",
                loader_cls=TextLoader,
                loader_kwargs={"encoding": "utf-8"},
            ).load()
        )

    if not documents:
        raise SystemExit(f"No PDF or TXT documents were found under {sources}")

    chunks = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=100).split_documents(
        documents
    )
    embeddings = HuggingFaceEmbeddings(
        model_name=settings.embedding_model,
        model_kwargs={"device": "cpu"},
    )
    Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name=settings.collection_name,
        persist_directory=str(destination),
    )
    print(f"Indexed {len(chunks)} chunks into {destination}")


if __name__ == "__main__":
    main()
