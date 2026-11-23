# Third-party attribution

Application source is MIT-licensed. Third-party assets retain their original terms.

- **Bitext customer support dataset**, Bitext, [publisher card](https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset), [CDLA-Sharing-1.0 terms](https://cdla.dev/sharing-1-0/). Source revision, original file and dataset card hashes are in `artifacts/source_manifest.json`. The original CSV and card are downloaded into ignored `work/raw`; instruction text is used for trained routing, not as retailer policy. Training outputs and derived model artifacts should be treated in accordance with the dataset's sharing terms. The original license applies to the source/derived data; the MIT application license does not replace it.
- **all-MiniLM-L6-v2**, sentence-transformers, [model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2), Apache-2.0. Pinned revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`; 384-dimensional embeddings. Quantized ONNX weights, tokenizer and model card are retained under `artifacts/embeddings` with hashes. This repository has not modified the model weights.
- DM Sans and Manrope are optional Google Fonts stylesheets. The UI has system-font fallbacks and does not require an external font to function.
- **oauth4webapi 3.8.9**, Filip Skokan, MIT. The browser authentication bundle retains the upstream copyright notice; the complete license is in `web/licenses/oauth4webapi-LICENSE.md`. It implements the OAuth/OIDC protocol checks used by the browser client.

All retailer policies, orders, customer subjects, organizations and evaluation cases are synthetic. There is no production customer data in the fixtures or saved browser recording.
