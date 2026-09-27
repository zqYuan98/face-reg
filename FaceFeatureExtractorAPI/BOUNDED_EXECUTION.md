# Bounded algorithm execution

The Java client admits at most two extraction calls and rejects further calls immediately with `429 ALGORITHM_BUSY`. Defaults are a 2 s connect timeout, 10 s without response progress, and 15 s total HTTP exchange budget. The total deadline cancels the actual JDK HTTP exchange and its response subscription, including a server continuously sending partial response bytes. Redirects are disabled. Responses are capped at 128 KiB and successful vectors must contain exactly 1024 finite numeric values with nonzero norm.

Java properties: `face.extractor.api.url`, `face.extractor.connect-timeout-ms`, `face.extractor.read-timeout-ms`, `face.extractor.total-timeout-ms`, `face.extractor.max-concurrent`, `face.extractor.max-image-bytes` (default 10 MiB), and `face.extractor.max-response-bytes`. Dependency timeout returns HTTP 504; unavailable or overloaded Python returns 503; malformed feature output returns 502; no usable face returns 422. Existing `extractFeature(File)` and `extractFeature(MultipartFile)` call signatures remain available.

Python extraction and detection share one native worker and one admission slot. Decoding and native inference run outside the ASGI event loop. A caller timeout or cancellation leaves the native task running and retains the slot until its actual completion. There is no waiting inference queue. `FACE_INFERENCE_TIMEOUT_SECONDS` defaults to 12 s and includes admitted upload reading and native execution; `FACE_INFERENCE_UNHEALTHY_SECONDS` defaults to 30 s; `FACE_MAX_IMAGE_BYTES` defaults to 10 MiB.

`GET /health` checks event-loop liveness and bypasses inference. `GET /ready` returns 503 while the model initializes, after initialization failure, or while an admitted operation exceeds the unhealthy threshold. A stuck operation reports `restart_required: true`. Deployment supervision must restart a process whose readiness remains unhealthy: thread cancellation cannot terminate permanently hung native code. Do not treat HTTP timeout as proof that ONNX work stopped. Run one application worker unless additional model memory and native concurrency have been measured. Keep the algorithm endpoint on the internal backend network and apply an upload-body limit at the ingress; FastAPI parses multipart uploads before handler admission.

Fault verification:

- `backend-java/src/test/java/com/bonuos/face/util/FaceFeatureExtractorClientTest.java`: real loopback HTTP server; trickling response total timeout with server-observed disconnect, caller interruption disconnect, no-header timeout, overload, response size bound, vector validation and no-face behavior.
- `tests/test_bounded_inference.py`: synthetic model; timeout/cancellation retain the native slot, live health and degraded readiness during a hung call, invalid/oversized uploads, initialization failure and shared extraction/detection admission.

The Python tests inject model and decoder substitutes and do not validate real ONNX dependencies, recognition quality, decoded-image memory peaks or production model latency. Test dependencies can be installed in an isolated directory; the existing application requirements are unchanged.

Cancellation semantics follow the [JDK 17 HttpClient contract](https://docs.oracle.com/en/java/javase/17/docs/api/java.net.http/java/net/http/HttpClient.html) and Python's [asyncio shield contract](https://docs.python.org/3/library/asyncio-task.html#asyncio.shield). JDK cancellation requests actual exchange termination, but does not promise to stop remote inference; Python retains its worker slot accordingly.
