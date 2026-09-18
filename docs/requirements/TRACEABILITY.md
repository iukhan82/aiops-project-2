# Assessment requirement traceability

Sources reviewed completely on 2026-09-18:

- `Ihsan Ullah_Exam Topic for Oral Presentation_DAIOL6.pdf` (6 pages), retained
  outside this repository because it is marked confidential and contains personal
  information.
- `Oral Presentation Exam Guide.pdf` (6 pages), retained outside this repository.

Status values: Planned, Implemented, Tested, Demonstrated, or Gap. Nothing below
is evidence merely because a task or file exists.

| ID | Normalized mandatory requirement | Planned evidence/tasks | Status |
|---|---|---|---|
| RQ01 | Design and implement an Edge AI and AIOps smart-city platform | Vertical-slice acceptance P12; repository release P13 | Planned |
| RQ02 | Process data locally at/near its source to reduce latency | Edge runtime, benchmarks, disconnection test P04 | Planned |
| RQ03 | Integrate edge with a centralized cloud/system tier for monitoring, analytics and automation | Hybrid decision P02; local central tier plus actual remote evidence if assessor requires it | Gap |
| RQ04 | Demonstrate real-time streams, scale and security | Load/latency/security acceptance P09-P12 | Planned |
| RQ05 | Use a lightweight compute environment at the edge | Containerized edge workloads and K3s/MicroK8s evidence P01/P11 | Planned |
| RQ06 | Run optimized AI inference on edge devices under limited resources | ONNX/OpenCV model, parity, latency/resource tests P04 | Planned |
| RQ07 | Monitor edge devices/apps, detect operational anomalies and trigger bounded automated responses | OTel/AIOps/remediation/recovery P10 | Planned |
| RQ08 | Network flow diagram with edge, gateways, central/cloud, users, protocols, secure channels and paths | Editable diagram and inspected export P02/P13 | Planned |
| RQ09 | Data flow diagram from sensing through edge, transmission, analysis and decision | Editable diagram and inspected export P02/P13 | Planned |
| RQ10 | Complete system architecture with devices, models, pipelines, monitoring and cloud integration | Tested implementation and proposed production diagrams P02/P13 | Planned |
| RQ11 | Process/workflow diagram | Incident-command-recovery and emergency-response diagrams P02/P13 | Planned |
| RQ12 | Security architecture and trust boundaries | Threat model plus editable diagram P09/P13 | Planned |
| RQ13 | Cloud/hybrid/on-prem architecture design and defensible placement | Decision record and diagram; deployed/proposed distinction P02/P13 | Planned |
| RQ14 | GitHub repository with edge deployment, model code, pipelines, monitoring, diagrams and detailed documentation | Publication review, release, authorized push P13 | Planned |
| RQ15 | K3s or MicroK8s plus Docker or Podman | Feasibility and real Linux deployment P01/P11 | Planned |
| RQ16 | Python plus TFLite, PyTorch Mobile, OpenCV or ONNX Runtime | Python/ONNX Runtime/OpenCV implementation evidence P04 | Planned |
| RQ17 | Kafka, Mosquitto or NiFi for real-time data | MQTT at edge plus central Kafka/Redpanda stream evidence P05 | Planned |
| RQ18 | Prometheus, Grafana and OpenTelemetry | Running dashboards, alerts, traces and restart survival P10/P11 | Planned |
| RQ19 | Keycloak, OPA, and Trivy or Falco | Identity/policy plus both scan and runtime evidence P09/P11 | Planned |
| RQ20 | Align with GDPR, ISO 27001 and relevant IoT security standards | Scoped control mapping, DPIA, risk register, gaps P09/P13 | Planned |
| RQ21 | Explain core concepts, why the solution exists, and Level 6 decisions/trade-offs | Decision records, speaker notes, rehearsals P02/P14 | Planned |
| RQ22 | Demonstrate configuration, deployment, operational behavior, failure/recovery, scale and security | Runbooks and target acceptance P10-P12 | Planned |
| RQ23 | Prepare policies, procedures, standards, controls, compliance, risk and governance rationale | Security/compliance package P09/P13 | Planned |
| RQ24 | Deliver a 15-20 minute presentation covering architecture, edge, AI, monitoring and automation | Timed deck and rehearsal P14 | Planned |
| RQ25 | Demonstrate real-time processing, edge AI, and response to an event/anomaly | Live demo plus offline backup P12/P14 | Planned |
| RQ26 | Prepare for a 30-40 minute interview on edge design, models, real-time challenges, scale and enterprise concerns | Question bank, mock interview and feedback P14 | Planned |
| RQ27 | Independently explain, defend, and modify/redesign architecture live | M2/M3/M4 walkthroughs and live design exercises P14 | Planned |
| RQ28 | Transparently use AI as a tool without replacing understanding/reasoning | AI-use log/statement and student defense P00/P14 | Planned |
| RQ29 | Optimize presentation for content, organization, delivery, visuals and responsible AI use | Rubric review and rendered-deck QA P14 | Planned |
| RQ30 | Prepare at least ten structured questions covering architecture, failure, security, performance and deployment | 25+ question bank and rehearsal P14 | Planned |

## Open assessment questions

- Is actual remote cloud execution required, or is a real locally deployed central
  tier plus proposed cloud architecture acceptable for RQ03?
- Are simulated traffic sensors and a synthetic map accepted if all downstream
  inference, streaming, security, monitoring and recovery are real?
- What is the submission deadline, upload format, repository access rule, and
  permitted demo environment?
