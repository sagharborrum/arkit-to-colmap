# Gaussian Splat Processing Service — System Specification

> Technical spec for building the automated scan-to-splat pipeline as a Firebase web app.

**Related:** [PIPELINE.md](./PIPELINE.md) — raw pipeline steps this system automates.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [Firestore Data Model](#firestore-data-model)
5. [Cloud Storage Layout](#cloud-storage-layout)
6. [SvelteKit Frontend](#sveltekit-frontend)
7. [Cloud Functions](#cloud-functions)
8. [GPU Worker](#gpu-worker)
9. [RunPod Integration](#runpod-integration)
10. [Job Lifecycle & State Machine](#job-lifecycle--state-machine)
11. [Progress Reporting Protocol](#progress-reporting-protocol)
12. [Error Handling & Recovery](#error-handling--recovery)
13. [Security Rules](#security-rules)
14. [Environment & Secrets](#environment--secrets)
15. [Cost Controls](#cost-controls)
16. [Build Order](#build-order)

---

## System Overview

A user uploads a 3D Scanner App export (ZIP), the system spins up a cloud GPU, runs the full COLMAP → gsplat pipeline, and delivers a browser-viewable Gaussian splat — all tracked in real-time.

**User flow:**
1. Upload ZIP from 3D Scanner App
2. See real-time progress (COLMAP → training → converting)
3. View finished splat in browser (interactive 3D)
4. Download .splat file

**System flow:**
1. SvelteKit accepts upload → Firebase Storage
2. Cloud Function creates job doc → spins up RunPod pod
3. GPU worker pulls data → runs pipeline → reports progress to Firestore
4. Worker uploads .splat → updates job → self-terminates
5. Frontend shows result via real-time Firestore listener

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        SvelteKit App                             │
│                     (Firebase Hosting)                            │
│                                                                  │
│  /upload          /jobs/:id              /view/:id               │
│  ┌──────────┐    ┌──────────────────┐   ┌──────────────────┐   │
│  │ Drop ZIP │    │ Real-time status │   │ 3D Splat Viewer  │   │
│  │ → Storage│    │ Progress bar     │   │ gaussian-splats  │   │
│  │ → Create │    │ Step indicator   │   │ -3d library      │   │
│  │   job    │    │ Cost tracker     │   │                  │   │
│  └────┬─────┘    └───────▲──────────┘   └───────▲──────────┘   │
│       │                  │ onSnapshot()         │ signed URL    │
└───────┼──────────────────┼──────────────────────┼───────────────┘
        │                  │                      │
   ┌────▼──────────────────┼──────────────────────┼───────┐
   │              Firebase Services                        │
   │                                                       │
   │  ┌────────────────┐  ┌─────────────────────────────┐ │
   │  │ Cloud Storage  │  │ Firestore                   │ │
   │  │                │  │                             │ │
   │  │ /uploads/      │  │ jobs/{id}                   │ │
   │  │   {jobId}.zip  │  │   .status                   │ │
   │  │                │  │   .progress                  │ │
   │  │ /results/      │  │   .step                      │ │
   │  │   {jobId}.splat│  │   .podId                     │ │
   │  │                │  │   .error                     │ │
   │  └────────────────┘  │   .timestamps{}              │ │
   │                      │   .metrics{}                  │ │
   │  ┌────────────────┐  └─────────────────────────────┘ │
   │  │ Cloud Functions │                                  │
   │  │                 │                                  │
   │  │ onJobCreated()  │─── Spin up RunPod pod           │
   │  │ onWorkerUpdate()│─── Validate progress reports     │
   │  │ watchdog()      │─── Kill stale pods (scheduled)   │
   │  │ onJobDone()     │─── Generate signed URL, cleanup  │
   │  └────────┬────────┘                                  │
   └───────────┼───────────────────────────────────────────┘
               │
               │  RunPod API (create/terminate pod)
               │  + Worker calls Firestore directly
               ▼
   ┌───────────────────────────────────────────────┐
   │            GPU Worker (RunPod Pod)             │
   │                                                │
   │  ┌──────────┐  ┌──────────┐  ┌─────────────┐ │
   │  │ Download │  │ COLMAP   │  │ gsplat      │ │
   │  │ ZIP from │─▶│ SfM      │─▶│ Training    │─┤
   │  │ Storage  │  │ (~2 min) │  │ (~45 min)   │ │
   │  └──────────┘  └──────────┘  └─────────────┘ │
   │                                     │         │
   │  ┌──────────────┐  ┌───────────────▼───────┐ │
   │  │ Upload .splat│◀─│ PLY → .splat convert │ │
   │  │ to Storage   │  │ + opacity pruning    │ │
   │  └──────┬───────┘  └─────────────────────┘ │
   │         │                                    │
   │  ┌──────▼───────┐                           │
   │  │ Update job   │                           │
   │  │ → "done"     │                           │
   │  │ Self-terminate│                          │
   │  └──────────────┘                           │
   └───────────────────────────────────────────────┘
```

---

## Project Structure

```
splat-service/
├── firebase.json                # Firebase config (hosting, functions, storage, firestore)
├── firestore.rules              # Security rules
├── firestore.indexes.json       # Composite indexes
├── storage.rules                # Storage security rules
│
├── src/                         # SvelteKit app
│   ├── lib/
│   │   ├── firebase.ts          # Firebase client SDK init
│   │   ├── stores/
│   │   │   └── job.ts           # Svelte store wrapping Firestore onSnapshot
│   │   ├── components/
│   │   │   ├── UploadZone.svelte
│   │   │   ├── JobProgress.svelte
│   │   │   ├── SplatViewer.svelte
│   │   │   └── JobList.svelte
│   │   └── utils/
│   │       ├── validation.ts    # Validate ZIP contents
│   │       └── format.ts        # Time, cost, file size formatters
│   │
│   ├── routes/
│   │   ├── +page.svelte         # Landing / upload
│   │   ├── +layout.svelte       # Shell (nav, auth)
│   │   ├── jobs/
│   │   │   ├── +page.svelte     # Job list / dashboard
│   │   │   └── [id]/
│   │   │       └── +page.svelte # Job detail + progress + viewer
│   │   └── api/
│   │       └── webhook/
│   │           └── +server.ts   # RunPod webhook endpoint (optional)
│   │
│   └── app.html
│
├── functions/                   # Firebase Cloud Functions (Node.js)
│   ├── src/
│   │   ├── index.ts             # Function exports
│   │   ├── onJobCreated.ts      # Firestore trigger: spin up pod
│   │   ├── watchdog.ts          # Scheduled: kill stale pods
│   │   ├── onJobCompleted.ts    # Firestore trigger: generate signed URL
│   │   └── runpod.ts            # RunPod API client
│   ├── package.json
│   └── tsconfig.json
│
├── worker/                      # GPU worker script (runs on RunPod)
│   ├── run_pipeline.py          # Main orchestrator
│   ├── arkit_to_colmap.py       # Step 1: format conversion
│   ├── run_colmap.sh            # Step 2: COLMAP SfM
│   ├── downscale_images.py      # Step 3: create images_2/
│   ├── train_gsplat.py          # Step 4: gsplat training wrapper
│   ├── export_splat.py          # Step 5: PLY → .splat conversion
│   ├── firebase_progress.py     # Firestore progress reporter
│   ├── requirements.txt         # Python deps
│   └── Dockerfile               # RunPod template image
│
├── package.json
├── svelte.config.js
├── vite.config.ts
└── .env.example
```

---

## Firestore Data Model

### `jobs/{jobId}`

```typescript
interface Job {
    // Identity
    id: string;                  // Auto-generated doc ID
    userId: string;              // Firebase Auth UID (if auth enabled, else 'anonymous')

    // Status
    status: JobStatus;
    step: PipelineStep;          // Current pipeline step
    progress: number;            // 0-100 (overall)
    stepProgress: number;        // 0-100 (within current step)

    // Input
    uploadPath: string;          // Storage path: "uploads/{jobId}.zip"
    uploadSizeBytes: number;
    imageCount: number | null;   // Populated after validation
    sceneName: string;           // User-provided or filename

    // Output
    resultPath: string | null;   // Storage path: "results/{jobId}.splat"
    resultUrl: string | null;    // Signed download URL (expires)
    resultSizeBytes: number | null;

    // GPU / Pod
    podId: string | null;        // RunPod pod ID
    podType: string | null;      // "NVIDIA RTX A5000" etc.
    podCostPerHour: number | null;

    // Metrics
    metrics: {
        colmapPoints: number | null;      // 3D points from SfM
        colmapError: number | null;       // Reprojection error (px)
        gaussianCount: number | null;     // Final Gaussian count
        trainingSteps: number | null;     // Steps completed
        trainingLoss: number | null;      // Final loss value
        prunedCount: number | null;       // Gaussians after pruning
    };

    // Cost
    gpuSeconds: number;          // Total GPU time used
    estimatedCost: number;       // USD

    // Timestamps
    createdAt: Timestamp;
    updatedAt: Timestamp;
    timestamps: {
        uploaded: Timestamp | null;
        podStarted: Timestamp | null;
        colmapStarted: Timestamp | null;
        colmapFinished: Timestamp | null;
        trainingStarted: Timestamp | null;
        trainingFinished: Timestamp | null;
        conversionFinished: Timestamp | null;
        completed: Timestamp | null;
    };

    // Error
    error: string | null;
    errorStep: PipelineStep | null;
    retryCount: number;
}

type JobStatus =
    | 'uploading'      // ZIP being uploaded to Storage
    | 'queued'         // Upload done, waiting for GPU
    | 'provisioning'   // RunPod pod spinning up
    | 'processing'     // Pipeline running on GPU
    | 'done'           // .splat ready to view
    | 'error'          // Something failed
    | 'cancelled';     // User cancelled

type PipelineStep =
    | 'upload'
    | 'validation'     // Checking ZIP contents
    | 'colmap'         // COLMAP SfM
    | 'training'       // gsplat training (longest step)
    | 'conversion'     // PLY → .splat
    | 'upload_result'  // Uploading .splat to Storage
    | 'cleanup';       // Pod termination
```

### Indexes Needed

```json
// firestore.indexes.json
{
    "indexes": [
        {
            "collectionGroup": "jobs",
            "queryScope": "COLLECTION",
            "fields": [
                { "fieldPath": "userId", "order": "ASCENDING" },
                { "fieldPath": "createdAt", "order": "DESCENDING" }
            ]
        },
        {
            "collectionGroup": "jobs",
            "queryScope": "COLLECTION",
            "fields": [
                { "fieldPath": "status", "order": "ASCENDING" },
                { "fieldPath": "updatedAt", "order": "ASCENDING" }
            ]
        }
    ]
}
```

---

## Cloud Storage Layout

```
gs://<bucket>/
├── uploads/
│   └── {jobId}.zip              # Raw scan export from user
├── results/
│   ├── {jobId}.splat            # Final compressed splat
│   └── {jobId}.ply              # Full PLY (optional, for re-processing)
└── temp/
    └── {jobId}/                 # Intermediate files (auto-deleted after 24h)
        ├── colmap_data.tar.gz
        └── colmap_images.tar.gz
```

### Lifecycle Rules

```json
{
    "lifecycle": {
        "rule": [
            {
                "action": { "type": "Delete" },
                "condition": {
                    "age": 1,
                    "matchesPrefix": ["temp/"]
                }
            },
            {
                "action": { "type": "Delete" },
                "condition": {
                    "age": 30,
                    "matchesPrefix": ["uploads/"]
                }
            }
        ]
    }
}
```

---

## SvelteKit Frontend

### Page: `/` — Upload

```svelte
<!-- src/routes/+page.svelte -->
<script lang="ts">
    import UploadZone from '$lib/components/UploadZone.svelte';
    import { goto } from '$app/navigation';
    import { collection, addDoc, serverTimestamp } from 'firebase/firestore';
    import { ref, uploadBytesResumable } from 'firebase/storage';
    import { db, storage } from '$lib/firebase';

    async function handleUpload(file: File) {
        // 1. Create job doc
        const jobRef = await addDoc(collection(db, 'jobs'), {
            status: 'uploading',
            step: 'upload',
            progress: 0,
            stepProgress: 0,
            sceneName: file.name.replace('.zip', ''),
            uploadSizeBytes: file.size,
            imageCount: null,
            resultPath: null,
            resultUrl: null,
            resultSizeBytes: null,
            podId: null,
            podType: null,
            podCostPerHour: null,
            metrics: {
                colmapPoints: null,
                colmapError: null,
                gaussianCount: null,
                trainingSteps: null,
                trainingLoss: null,
                prunedCount: null,
            },
            gpuSeconds: 0,
            estimatedCost: 0,
            createdAt: serverTimestamp(),
            updatedAt: serverTimestamp(),
            timestamps: {},
            error: null,
            errorStep: null,
            retryCount: 0,
            userId: 'anonymous', // or auth.currentUser.uid
        });

        const jobId = jobRef.id;
        const uploadPath = `uploads/${jobId}.zip`;

        // 2. Upload ZIP to Storage
        const storageRef = ref(storage, uploadPath);
        const uploadTask = uploadBytesResumable(storageRef, file);

        uploadTask.on('state_changed', (snapshot) => {
            const pct = (snapshot.bytesTransferred / snapshot.totalBytes) * 100;
            // Update progress locally (no need to write to Firestore for upload %)
        });

        await uploadTask;

        // 3. Update job doc — this triggers the Cloud Function
        await updateDoc(jobRef, {
            status: 'queued',
            step: 'validation',
            uploadPath,
            'timestamps.uploaded': serverTimestamp(),
            updatedAt: serverTimestamp(),
        });

        // 4. Navigate to job page
        goto(`/jobs/${jobId}`);
    }
</script>
```

### Page: `/jobs/[id]` — Progress + Viewer

```svelte
<!-- src/routes/jobs/[id]/+page.svelte -->
<script lang="ts">
    import { page } from '$app/stores';
    import { doc, onSnapshot } from 'firebase/firestore';
    import { db } from '$lib/firebase';
    import JobProgress from '$lib/components/JobProgress.svelte';
    import SplatViewer from '$lib/components/SplatViewer.svelte';

    let job = null;
    let unsubscribe;

    $: {
        const jobId = $page.params.id;
        unsubscribe?.();
        unsubscribe = onSnapshot(doc(db, 'jobs', jobId), (snap) => {
            job = { id: snap.id, ...snap.data() };
        });
    }
</script>

{#if job}
    {#if job.status === 'done'}
        <SplatViewer url={job.resultUrl} />
    {:else if job.status === 'error'}
        <div class="error">
            <h2>Processing Failed</h2>
            <p>Step: {job.errorStep}</p>
            <p>{job.error}</p>
        </div>
    {:else}
        <JobProgress {job} />
    {/if}
{/if}
```

### Component: `JobProgress.svelte`

```svelte
<script lang="ts">
    export let job;

    const STEPS = [
        { key: 'validation', label: 'Validating scan', icon: '📋' },
        { key: 'colmap',     label: 'Structure from Motion', icon: '📐' },
        { key: 'training',   label: 'Training Gaussians', icon: '🧠' },
        { key: 'conversion', label: 'Compressing', icon: '📦' },
    ];

    $: currentStepIndex = STEPS.findIndex(s => s.key === job.step);
    $: overallProgress = job.progress;
    $: eta = estimateETA(job);

    function estimateETA(job) {
        if (job.step !== 'training' || !job.stepProgress) return null;
        const started = job.timestamps?.trainingStarted?.toMillis();
        if (!started) return null;
        const elapsed = Date.now() - started;
        const remaining = (elapsed / job.stepProgress) * (100 - job.stepProgress);
        return Math.round(remaining / 60000); // minutes
    }
</script>

<div class="progress-container">
    <!-- Step indicators -->
    <div class="steps">
        {#each STEPS as step, i}
            <div class="step"
                 class:active={i === currentStepIndex}
                 class:done={i < currentStepIndex}>
                <span class="icon">{step.icon}</span>
                <span class="label">{step.label}</span>
                {#if i === currentStepIndex && job.stepProgress > 0}
                    <span class="pct">{Math.round(job.stepProgress)}%</span>
                {/if}
            </div>
        {/each}
    </div>

    <!-- Overall progress bar -->
    <div class="bar">
        <div class="fill" style="width: {overallProgress}%"></div>
    </div>

    <!-- Stats -->
    <div class="stats">
        {#if job.metrics?.colmapPoints}
            <div>📍 {job.metrics.colmapPoints.toLocaleString()} 3D points</div>
        {/if}
        {#if job.metrics?.gaussianCount}
            <div>💠 {job.metrics.gaussianCount.toLocaleString()} Gaussians</div>
        {/if}
        {#if job.metrics?.trainingSteps}
            <div>🔄 Step {job.metrics.trainingSteps.toLocaleString()} / 30,000</div>
        {/if}
        {#if eta}
            <div>⏱️ ~{eta} min remaining</div>
        {/if}
        {#if job.estimatedCost > 0}
            <div>💰 ${job.estimatedCost.toFixed(2)}</div>
        {/if}
    </div>
</div>
```

### Component: `SplatViewer.svelte`

```svelte
<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    export let url: string;

    let container: HTMLDivElement;
    let viewer;

    onMount(async () => {
        const GS = await import('@mkkellogg/gaussian-splats-3d');
        viewer = new GS.Viewer({
            cameraUp: [0, -1, 0],
            initialCameraPosition: [0, -3, -3],
            initialCameraLookAt: [0, 0, 1],
            sharedMemoryForWorkers: false,
            selfDrivenMode: true,
            rootElement: container,
        });

        await viewer.addSplatScene(url, {
            splatAlphaRemovalThreshold: 20,
            progressiveLoad: true,
        });
        viewer.start();
    });

    onDestroy(() => {
        viewer?.dispose();
    });
</script>

<div bind:this={container} class="viewer"></div>

<style>
    .viewer { width: 100%; height: 80vh; }
</style>
```

---

## Cloud Functions

### `onJobCreated` — Spin Up GPU Pod

Triggered when a job transitions to `queued` status.

```typescript
// functions/src/onJobCreated.ts
import { onDocumentUpdated } from 'firebase-functions/v2/firestore';
import { createPod, POD_CONFIG } from './runpod';

export const onJobQueued = onDocumentUpdated('jobs/{jobId}', async (event) => {
    const before = event.data?.before.data();
    const after = event.data?.after.data();
    if (!after || before?.status === after.status) return;
    if (after.status !== 'queued') return;

    const jobId = event.params.jobId;
    const jobRef = event.data.after.ref;

    try {
        // Update status
        await jobRef.update({
            status: 'provisioning',
            updatedAt: FieldValue.serverTimestamp(),
        });

        // Create RunPod pod
        const pod = await createPod({
            name: `splat-${jobId}`,
            gpuTypeId: POD_CONFIG.gpuTypeId,
            templateId: POD_CONFIG.templateId,
            cloudType: 'COMMUNITY',
            volumeInGb: 50,
            containerDiskInGb: 20,
            env: {
                JOB_ID: jobId,
                FIREBASE_PROJECT: process.env.FIREBASE_PROJECT,
                FIREBASE_BUCKET: process.env.FIREBASE_BUCKET,
                // Service account key passed as env var (base64)
                FIREBASE_SA_KEY: process.env.FIREBASE_SA_KEY_B64,
            },
        });

        await jobRef.update({
            podId: pod.id,
            podType: POD_CONFIG.gpuDisplayName,
            podCostPerHour: POD_CONFIG.costPerHour,
            'timestamps.podStarted': FieldValue.serverTimestamp(),
            updatedAt: FieldValue.serverTimestamp(),
        });

    } catch (err) {
        await jobRef.update({
            status: 'error',
            error: `Failed to provision GPU: ${err.message}`,
            errorStep: 'validation',
            updatedAt: FieldValue.serverTimestamp(),
        });
    }
});
```

### `watchdog` — Kill Stale Pods

Runs every 5 minutes. Catches pods that stopped reporting progress.

```typescript
// functions/src/watchdog.ts
import { onSchedule } from 'firebase-functions/v2/scheduler';
import { terminatePod } from './runpod';

export const watchdog = onSchedule('every 5 minutes', async () => {
    const db = getFirestore();
    const staleThreshold = Date.now() - 15 * 60 * 1000; // 15 min no update

    // Find processing jobs that haven't updated recently
    const staleJobs = await db.collection('jobs')
        .where('status', 'in', ['provisioning', 'processing'])
        .where('updatedAt', '<', Timestamp.fromMillis(staleThreshold))
        .get();

    for (const doc of staleJobs.docs) {
        const job = doc.data();
        console.warn(`Stale job ${doc.id}, pod ${job.podId}, last update ${job.updatedAt.toDate()}`);

        // Kill the pod
        if (job.podId) {
            try {
                await terminatePod(job.podId);
            } catch (e) {
                console.error(`Failed to terminate pod ${job.podId}:`, e);
            }
        }

        // Mark as error
        await doc.ref.update({
            status: 'error',
            error: 'Processing stalled — pod terminated. You can retry.',
            errorStep: job.step,
            updatedAt: FieldValue.serverTimestamp(),
        });
    }

    // Also find jobs stuck in 'queued' for > 10 min (pod never started)
    const queuedThreshold = Date.now() - 10 * 60 * 1000;
    const stuckQueued = await db.collection('jobs')
        .where('status', '==', 'queued')
        .where('updatedAt', '<', Timestamp.fromMillis(queuedThreshold))
        .get();

    for (const doc of stuckQueued.docs) {
        await doc.ref.update({
            status: 'error',
            error: 'Failed to provision GPU. Please retry.',
            errorStep: 'validation',
            updatedAt: FieldValue.serverTimestamp(),
        });
    }
});
```

### `onJobCompleted` — Generate Download URL

```typescript
// functions/src/onJobCompleted.ts
export const onJobCompleted = onDocumentUpdated('jobs/{jobId}', async (event) => {
    const before = event.data?.before.data();
    const after = event.data?.after.data();
    if (!after || before?.status === after.status) return;
    if (after.status !== 'done' || !after.resultPath) return;

    const bucket = getStorage().bucket();
    const file = bucket.file(after.resultPath);

    // Generate signed URL (valid 7 days)
    const [url] = await file.getSignedUrl({
        action: 'read',
        expires: Date.now() + 7 * 24 * 60 * 60 * 1000,
    });

    await event.data.after.ref.update({
        resultUrl: url,
        updatedAt: FieldValue.serverTimestamp(),
    });
});
```

### `runpod.ts` — API Client

```typescript
// functions/src/runpod.ts
const RUNPOD_API = 'https://api.runpod.io/graphql';
const RUNPOD_KEY = process.env.RUNPOD_API_KEY;

export const POD_CONFIG = {
    gpuTypeId: 'NVIDIA RTX A5000',   // 24GB, $0.16/hr community
    gpuDisplayName: 'RTX A5000',
    costPerHour: 0.16,
    templateId: 'YOUR_TEMPLATE_ID',  // Pre-built Docker image
};

export async function createPod(opts: {
    name: string;
    gpuTypeId: string;
    templateId: string;
    cloudType: string;
    volumeInGb: number;
    containerDiskInGb: number;
    env: Record<string, string>;
}) {
    const envArray = Object.entries(opts.env).map(([key, value]) => ({
        key, value,
    }));

    const query = `
        mutation {
            podFindAndDeployOnDemand(input: {
                name: "${opts.name}"
                gpuTypeId: "${opts.gpuTypeId}"
                templateId: "${opts.templateId}"
                cloudType: ${opts.cloudType}
                volumeInGb: ${opts.volumeInGb}
                containerDiskInGb: ${opts.containerDiskInGb}
                env: ${JSON.stringify(envArray)}
            }) {
                id
                desiredStatus
                runtime { uptimeInSeconds }
            }
        }
    `;

    const res = await fetch(RUNPOD_API, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${RUNPOD_KEY}`,
        },
        body: JSON.stringify({ query }),
    });

    const data = await res.json();
    if (data.errors) throw new Error(data.errors[0].message);
    return data.data.podFindAndDeployOnDemand;
}

export async function terminatePod(podId: string) {
    const query = `mutation { podTerminate(input: { podId: "${podId}" }) }`;

    const res = await fetch(RUNPOD_API, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${RUNPOD_KEY}`,
        },
        body: JSON.stringify({ query }),
    });

    const data = await res.json();
    if (data.errors) throw new Error(data.errors[0].message);
}
```

---

## GPU Worker

The worker runs automatically when the RunPod pod starts. It reads `JOB_ID` from environment, pulls the ZIP from Firebase Storage, runs the full pipeline, and reports progress back to Firestore.

### `run_pipeline.py` — Main Orchestrator

```python
#!/usr/bin/env python3
"""
GPU Worker — Main pipeline orchestrator.
Runs automatically when RunPod pod starts.

Environment variables:
    JOB_ID            - Firestore job document ID
    FIREBASE_PROJECT  - Firebase project ID
    FIREBASE_BUCKET   - Cloud Storage bucket name
    FIREBASE_SA_KEY   - Base64-encoded service account JSON
"""

import os
import sys
import json
import base64
import subprocess
import time
import traceback

import firebase_admin
from firebase_admin import credentials, firestore, storage
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

# ── Firebase Init ──────────────────────────────────────────

JOB_ID = os.environ['JOB_ID']
PROJECT = os.environ['FIREBASE_PROJECT']
BUCKET = os.environ['FIREBASE_BUCKET']

sa_key = json.loads(base64.b64decode(os.environ['FIREBASE_SA_KEY']))
cred = credentials.Certificate(sa_key)
firebase_admin.initialize_app(cred, {'storageBucket': BUCKET})

db = firestore.client()
bucket = storage.bucket()
job_ref = db.collection('jobs').document(JOB_ID)

WORK_DIR = '/workspace/pipeline'
os.makedirs(WORK_DIR, exist_ok=True)


# ── Progress Reporter ──────────────────────────────────────

def update_job(**kwargs):
    """Update job document in Firestore."""
    kwargs['updatedAt'] = SERVER_TIMESTAMP
    job_ref.update(kwargs)


def set_step(step: str, progress: int = None):
    """Update current pipeline step."""
    update = {'step': step, 'status': 'processing'}
    if progress is not None:
        update['progress'] = progress
    update_job(**update)


def set_step_progress(step_progress: int, overall_progress: int = None,
                       metrics: dict = None):
    """Update progress within current step."""
    update = {'stepProgress': step_progress}
    if overall_progress is not None:
        update['progress'] = overall_progress
    if metrics:
        for k, v in metrics.items():
            update[f'metrics.{k}'] = v
    update_job(**update)


# ── Pipeline Steps ─────────────────────────────────────────

def step_download():
    """Download and extract upload ZIP from Firebase Storage."""
    set_step('validation', progress=5)

    job = job_ref.get().to_dict()
    upload_path = job['uploadPath']

    zip_path = f'{WORK_DIR}/scan.zip'
    blob = bucket.blob(upload_path)
    blob.download_to_filename(zip_path)

    # Extract
    subprocess.run(['unzip', '-o', zip_path, '-d', f'{WORK_DIR}/scan_data'],
                   check=True)

    # Find the inner timestamp folder (3D Scanner App nests one level)
    import glob
    json_files = glob.glob(f'{WORK_DIR}/scan_data/**/frame_*.json', recursive=True)
    if not json_files:
        raise ValueError('No frame_*.json files found in ZIP. Is this a 3D Scanner App export?')

    scan_dir = os.path.dirname(json_files[0])
    jpg_count = len(glob.glob(f'{scan_dir}/frame_*.jpg'))

    update_job(imageCount=jpg_count)
    set_step_progress(100, overall_progress=8)

    return scan_dir


def step_arkit_to_colmap(scan_dir: str):
    """Convert ARKit export to COLMAP format."""
    set_step('colmap', progress=10)

    output_dir = f'{WORK_DIR}/colmap_project'
    subprocess.run([
        'python3', '/workspace/worker/arkit_to_colmap.py',
        scan_dir, '-o', output_dir,
    ], check=True)

    set_step_progress(20, overall_progress=12)
    return output_dir


def step_colmap_sfm(project_dir: str):
    """Run COLMAP Structure-from-Motion."""
    db_path = f'{project_dir}/database.db'
    image_path = f'{project_dir}/images'
    sparse_path = f'{project_dir}/sparse/0'

    # Feature extraction
    set_step_progress(30, overall_progress=14)
    subprocess.run([
        'colmap', 'feature_extractor',
        '--database_path', db_path,
        '--image_path', image_path,
        '--ImageReader.camera_model', 'PINHOLE',
        '--ImageReader.single_camera', '1',
    ], check=True)

    # Matching
    set_step_progress(50, overall_progress=16)
    subprocess.run([
        'colmap', 'exhaustive_matcher',
        '--database_path', db_path,
    ], check=True)

    # Triangulation (using ARKit poses)
    set_step_progress(70, overall_progress=18)
    subprocess.run([
        'colmap', 'point_triangulator',
        '--database_path', db_path,
        '--image_path', image_path,
        '--input_path', sparse_path,
        '--output_path', sparse_path,
    ], check=True)

    # Convert to binary
    subprocess.run([
        'colmap', 'model_converter',
        '--input_path', sparse_path,
        '--output_path', sparse_path,
        '--output_type', 'BIN',
    ], check=True)

    # Get stats
    result = subprocess.run(
        ['colmap', 'model_analyzer', '--path', sparse_path],
        capture_output=True, text=True
    )
    # Parse point count from output
    points = None
    error = None
    for line in result.stdout.split('\n'):
        if 'Points' in line and ':' in line:
            points = int(line.split(':')[1].strip())
        if 'Mean reprojection error' in line:
            error = float(line.split(':')[1].strip().replace('px', ''))

    set_step_progress(100, overall_progress=20, metrics={
        'colmapPoints': points,
        'colmapError': error,
    })
    update_job(**{'timestamps.colmapFinished': SERVER_TIMESTAMP})

    return project_dir


def step_downscale(project_dir: str, factor: int = 2):
    """Create downscaled images for training."""
    images_dir = f'{project_dir}/images'
    out_dir = f'{project_dir}/images_{factor}'
    os.makedirs(out_dir, exist_ok=True)

    from PIL import Image
    import glob

    files = sorted(glob.glob(f'{images_dir}/*.jpg'))
    for i, f in enumerate(files):
        img = Image.open(f)
        new_size = (img.width // factor, img.height // factor)
        img.resize(new_size, Image.LANCZOS).save(
            f'{out_dir}/{os.path.basename(f)}', quality=95
        )

    return factor


def step_train(project_dir: str, data_factor: int):
    """Run gsplat training. This is the long step (~45 min)."""
    set_step('training', progress=20)
    update_job(**{'timestamps.trainingStarted': SERVER_TIMESTAMP})

    result_dir = f'{WORK_DIR}/results'
    max_steps = 30000

    # Start training as subprocess so we can monitor the log
    log_path = f'{WORK_DIR}/training.log'
    proc = subprocess.Popen(
        [
            'python3', '/workspace/gsplat/examples/simple_trainer.py', 'default',
            '--data_dir', project_dir,
            '--data_factor', str(data_factor),
            '--result_dir', result_dir,
            '--max_steps', str(max_steps),
        ],
        stdout=open(log_path, 'w'),
        stderr=subprocess.STDOUT,
    )

    # Monitor progress by reading log
    last_step = 0
    while proc.poll() is None:
        time.sleep(30)  # Check every 30 seconds

        try:
            with open(log_path) as f:
                lines = f.readlines()

            for line in reversed(lines):
                if 'step=' in line or 'Step' in line:
                    # Parse step number from gsplat output
                    import re
                    match = re.search(r'(?:step|Step)[=:\s]+(\d+)', line)
                    if match:
                        step = int(match.group(1))
                        if step > last_step:
                            last_step = step
                            step_pct = min(99, int(step / max_steps * 100))
                            # Training is 20-90% of overall progress
                            overall = 20 + int(step_pct * 0.70)
                            set_step_progress(step_pct, overall_progress=overall,
                                              metrics={
                                                  'trainingSteps': step,
                                              })
                    break

                # Parse loss
                loss_match = re.search(r'loss[=:\s]+([\d.]+)', line)
                if loss_match:
                    update_job(**{'metrics.trainingLoss': float(loss_match.group(1))})
        except Exception:
            pass

    if proc.returncode != 0:
        raise RuntimeError(f'gsplat training failed with code {proc.returncode}')

    set_step_progress(100, overall_progress=90, metrics={
        'trainingSteps': max_steps,
    })
    update_job(**{'timestamps.trainingFinished': SERVER_TIMESTAMP})

    return result_dir


def step_convert(result_dir: str):
    """Convert trained PLY to .splat format with opacity pruning."""
    set_step('conversion', progress=90)

    # Find the checkpoint / PLY
    import glob
    ply_files = glob.glob(f'{result_dir}/**/*.ply', recursive=True)
    ckpt_files = glob.glob(f'{result_dir}/**/*.pt', recursive=True)

    if ckpt_files:
        # Export PLY from checkpoint
        ply_path = f'{WORK_DIR}/output.ply'
        subprocess.run([
            'python3', '/workspace/worker/export_splat.py',
            'from-checkpoint', ckpt_files[0], ply_path,
        ], check=True)
    elif ply_files:
        ply_path = ply_files[0]
    else:
        raise FileNotFoundError('No PLY or checkpoint found in results')

    # Convert PLY → .splat with pruning
    splat_path = f'{WORK_DIR}/output.splat'
    subprocess.run([
        'python3', '/workspace/worker/export_splat.py',
        'to-splat', ply_path, splat_path,
        '--opacity-threshold', '0.1',
    ], check=True)

    # Get stats
    splat_size = os.path.getsize(splat_path)
    gaussian_count = splat_size // 32  # 32 bytes per Gaussian

    set_step_progress(100, overall_progress=95, metrics={
        'prunedCount': gaussian_count,
        'gaussianCount': gaussian_count,
    })
    update_job(**{'timestamps.conversionFinished': SERVER_TIMESTAMP})

    return splat_path


def step_upload_result(splat_path: str):
    """Upload .splat to Firebase Storage."""
    set_step('upload_result', progress=95)

    result_path = f'results/{JOB_ID}.splat'
    blob = bucket.blob(result_path)
    blob.upload_from_filename(splat_path)

    splat_size = os.path.getsize(splat_path)

    update_job(
        resultPath=result_path,
        resultSizeBytes=splat_size,
    )
    set_step_progress(100, overall_progress=98)

    return result_path


def step_complete():
    """Mark job as done and calculate cost."""
    job = job_ref.get().to_dict()

    # Calculate GPU time
    pod_started = job.get('timestamps', {}).get('podStarted')
    if pod_started:
        gpu_seconds = int(time.time() - pod_started.timestamp())
        cost_per_hour = job.get('podCostPerHour', 0.16)
        cost = (gpu_seconds / 3600) * cost_per_hour
    else:
        gpu_seconds = 0
        cost = 0

    update_job(
        status='done',
        step='cleanup',
        progress=100,
        stepProgress=100,
        gpuSeconds=gpu_seconds,
        estimatedCost=round(cost, 4),
        **{'timestamps.completed': SERVER_TIMESTAMP},
    )


def self_terminate():
    """Terminate this RunPod pod."""
    pod_id = os.environ.get('RUNPOD_POD_ID')
    if not pod_id:
        print('RUNPOD_POD_ID not set, skipping self-termination')
        return

    api_key = os.environ.get('RUNPOD_API_KEY')
    if not api_key:
        print('RUNPOD_API_KEY not set, skipping self-termination')
        return

    import requests
    requests.post('https://api.runpod.io/graphql', json={
        'query': f'mutation {{ podTerminate(input: {{ podId: "{pod_id}" }}) }}'
    }, headers={
        'Authorization': f'Bearer {api_key}',
    })


# ── Main ───────────────────────────────────────────────────

def main():
    try:
        print(f'Starting pipeline for job {JOB_ID}')
        update_job(status='processing')

        scan_dir = step_download()
        project_dir = step_arkit_to_colmap(scan_dir)
        step_colmap_sfm(project_dir)
        data_factor = step_downscale(project_dir, factor=2)
        result_dir = step_train(project_dir, data_factor)
        splat_path = step_convert(result_dir)
        step_upload_result(splat_path)
        step_complete()

        print(f'Job {JOB_ID} completed successfully!')

    except Exception as e:
        traceback.print_exc()
        job_data = job_ref.get().to_dict()
        update_job(
            status='error',
            error=str(e)[:500],
            errorStep=job_data.get('step', 'unknown'),
        )

    finally:
        # Always try to self-terminate
        self_terminate()


if __name__ == '__main__':
    main()
```

### Worker Dockerfile (RunPod Template)

```dockerfile
# worker/Dockerfile
# Pre-built image for RunPod template — has all deps installed
FROM runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel

# System deps
RUN apt-get update && apt-get install -y \
    colmap \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# gsplat (locked version)
RUN pip install gsplat==1.5.3
RUN git clone https://github.com/nerfstudio-project/gsplat.git /workspace/gsplat \
    && cd /workspace/gsplat && git checkout v1.5.3

# Worker scripts
COPY . /workspace/worker/

# Entry point: run pipeline on pod start
CMD ["python3", "/workspace/worker/run_pipeline.py"]
```

### `requirements.txt`

```
firebase-admin>=6.0
numpy
Pillow
scipy
tqdm
plyfile
imageio
tyro
fused-ssim
```

---

## RunPod Integration

### Pod Template Setup (One-Time)

1. Build the Docker image and push to Docker Hub:
   ```bash
   cd worker/
   docker build -t youraccount/splat-worker:latest .
   docker push youraccount/splat-worker:latest
   ```

2. Create a RunPod template:
   - **Container Image:** `youraccount/splat-worker:latest`
   - **Container Start Command:** `python3 /workspace/worker/run_pipeline.py`
   - **Volume Mount Path:** `/workspace`
   - **Expose HTTP/TCP:** Not needed (worker connects outbound only)

3. Save the template ID → use in Cloud Functions config.

### GPU Selection Strategy

```typescript
// Preferred GPUs in order (price/performance for this workload)
const GPU_PREFERENCES = [
    { id: 'NVIDIA RTX A5000',     vram: 24, cost: 0.16 },  // Best value
    { id: 'NVIDIA RTX A4000',     vram: 16, cost: 0.17 },  // Slightly less VRAM
    { id: 'NVIDIA GeForce RTX 3090', vram: 24, cost: 0.22 },
    { id: 'NVIDIA RTX 4000 Ada Generation', vram: 20, cost: 0.24 },
];
```

### Pod Lifecycle

```
create pod (Cloud Function)
    ↓
pod initializes (~30s)
    ↓
Docker CMD runs run_pipeline.py
    ↓
pipeline executes (~50 min)
    ↓
worker calls self-terminate
    ↓
pod destroyed (billing stops)
```

If self-termination fails, the watchdog Cloud Function catches it within 15 minutes.

---

## Job Lifecycle & State Machine

```
                    ┌──────────────┐
                    │  uploading   │  User uploading ZIP to Storage
                    └──────┬───────┘
                           │ Upload complete
                    ┌──────▼───────┐
                    │   queued     │  Cloud Function triggered
                    └──────┬───────┘
                           │ Pod creation API call sent
                    ┌──────▼───────┐
                    │ provisioning │  Waiting for RunPod pod
                    └──────┬───────┘
                           │ Pod running, worker starts
                    ┌──────▼───────┐
         ┌──────────│  processing  │──────────┐
         │          └──────┬───────┘          │
         │                 │ All steps done    │ Any step fails
         │          ┌──────▼───────┐   ┌──────▼───────┐
         │          │    done      │   │    error     │
         │          └──────────────┘   └──────┬───────┘
         │                                     │ User retries
         │          ┌──────────────┐           │
         └──────────│  cancelled   │◀──────────┘ (optional)
                    └──────────────┘

Processing sub-states (tracked via `step` field):
    validation → colmap → training → conversion → upload_result → cleanup
```

### Progress Mapping

| Step | Step % Range | Overall % Range | Duration |
|------|-------------|-----------------|----------|
| Upload | 0-100 | 0-5 | ~10s |
| Validation | 0-100 | 5-8 | ~5s |
| COLMAP SfM | 0-100 | 10-20 | ~2 min |
| Training | 0-100 | 20-90 | ~45 min |
| Conversion | 0-100 | 90-95 | ~30s |
| Upload Result | 0-100 | 95-98 | ~10s |
| Cleanup | — | 98-100 | immediate |

Training dominates (70% of overall progress), so the progress bar will move slowly during that phase. The `stepProgress` field lets the UI show a sub-progress bar within the training step.

---

## Error Handling & Recovery

### Error Types

| Error | Where | Recovery |
|-------|-------|----------|
| Invalid ZIP (no frames) | Worker: validation | Show error, let user re-upload |
| Too few images (<10) | Worker: validation | Show error with minimum requirement |
| COLMAP fails (insufficient matches) | Worker: colmap | Suggest better capture technique |
| GPU OOM during training | Worker: training | Retry with `--data_factor 4` |
| Pod never starts | Cloud Function | Watchdog kills after 10 min, user retries |
| Pod dies mid-training | Worker/Watchdog | Mark error, offer retry |
| Firebase Storage upload fails | Worker: upload | Retry 3x with exponential backoff |
| Self-termination fails | Watchdog | Watchdog catches within 15 min |

### Retry Logic

```typescript
// In Cloud Functions or triggered by user
async function retryJob(jobId: string) {
    const jobRef = db.collection('jobs').doc(jobId);
    const job = (await jobRef.get()).data();

    if (job.retryCount >= 3) {
        throw new Error('Maximum retries exceeded');
    }

    await jobRef.update({
        status: 'queued',
        step: 'validation',
        progress: 0,
        stepProgress: 0,
        error: null,
        errorStep: null,
        podId: null,
        retryCount: (job.retryCount || 0) + 1,
        updatedAt: serverTimestamp(),
    });
    // This triggers onJobQueued again
}
```

---

## Security Rules

### Firestore

```javascript
// firestore.rules
rules_version = '2';
service cloud.firestore {
    match /databases/{database}/documents {

        // Jobs: users can read their own, only Cloud Functions can write most fields
        match /jobs/{jobId} {
            // Anyone can create a job (or restrict to auth'd users)
            allow create: if true;
            // Users can read their own jobs
            allow read: if true;  // or: resource.data.userId == request.auth.uid
            // Only server (admin SDK) can update
            allow update: if false;
            allow delete: if false;
        }
    }
}
```

### Cloud Storage

```javascript
// storage.rules
rules_version = '2';
service firebase.storage {
    match /b/{bucket}/o {

        // Users can upload to uploads/
        match /uploads/{fileName} {
            allow write: if request.resource.size < 500 * 1024 * 1024  // 500MB max
                && request.resource.contentType == 'application/zip';
            allow read: if false;  // Only server reads uploads
        }

        // Results are read via signed URLs (no direct access needed)
        match /results/{fileName} {
            allow read: if false;   // Signed URLs bypass rules
            allow write: if false;  // Only server writes
        }
    }
}
```

---

## Environment & Secrets

### `.env` (SvelteKit / local dev)

```bash
# Firebase (client-side, safe to expose)
PUBLIC_FIREBASE_API_KEY=AIza...
PUBLIC_FIREBASE_AUTH_DOMAIN=yourapp.firebaseapp.com
PUBLIC_FIREBASE_PROJECT_ID=yourapp
PUBLIC_FIREBASE_STORAGE_BUCKET=yourapp.appspot.com
PUBLIC_FIREBASE_MESSAGING_SENDER_ID=123456
PUBLIC_FIREBASE_APP_ID=1:123456:web:abc123
```

### Cloud Functions Secrets (Firebase Secret Manager)

```bash
# Set via: firebase functions:secrets:set SECRET_NAME
RUNPOD_API_KEY=rpa_...
FIREBASE_SA_KEY_B64=eyJ0eXBlIjoic2Vydi...  # base64(service-account.json)
```

### RunPod Pod Environment (set by Cloud Function at creation)

```bash
JOB_ID=abc123                    # Which job to process
FIREBASE_PROJECT=yourapp         # Firebase project ID
FIREBASE_BUCKET=yourapp.appspot.com
FIREBASE_SA_KEY=eyJ0eXBl...     # base64 service account key
RUNPOD_POD_ID=xyz789            # Set by RunPod automatically
RUNPOD_API_KEY=rpa_...          # For self-termination
```

---

## Cost Controls

### Per-Job Limits

```typescript
const LIMITS = {
    maxGpuMinutes: 90,          // Hard kill after 90 min
    maxUploadSizeMB: 500,       // Reject larger uploads
    maxConcurrentJobs: 3,       // Queue additional jobs
    maxRetriesPerJob: 3,
};
```

### Budget Watchdog

Add to the scheduled watchdog function:

```typescript
// Check total spend this month
const monthStart = new Date();
monthStart.setDate(1);
monthStart.setHours(0, 0, 0, 0);

const completedJobs = await db.collection('jobs')
    .where('status', '==', 'done')
    .where('timestamps.completed', '>=', monthStart)
    .get();

const totalSpend = completedJobs.docs.reduce(
    (sum, doc) => sum + (doc.data().estimatedCost || 0), 0
);

if (totalSpend > 50) {  // $50/month budget
    console.error(`Monthly budget exceeded: $${totalSpend}`);
    // Could pause new job creation
}
```

### Estimated Costs Per Job

| Component | Cost |
|-----------|------|
| GPU (45 min × $0.16/hr) | ~$0.12 |
| Firebase Storage (200MB) | ~$0.005 |
| Firestore (100 writes) | ~$0.00001 |
| Cloud Functions (60s) | ~$0.0001 |
| **Total per job** | **~$0.13** |

---

## Build Order

Recommended order for building this out:

### Phase 1: Foundation
1. **Firebase project setup** — Hosting, Firestore, Storage, Functions
2. **SvelteKit scaffold** — `npm create svelte@latest`, add Firebase SDK
3. **Firestore schema** — Create a test job doc manually, verify rules
4. **Upload page** — ZIP upload to Storage + job doc creation

### Phase 2: GPU Worker
5. **Worker script** — Port `run_pipeline.py` with Firebase progress reporting
6. **Test locally** — Run worker script manually on a RunPod pod with a test job
7. **Docker image** — Build and push worker image
8. **RunPod template** — Create template with the Docker image

### Phase 3: Orchestration
9. **`onJobQueued` function** — Auto-create RunPod pod when job is queued
10. **`watchdog` function** — Scheduled stale pod killer
11. **`onJobCompleted` function** — Generate signed download URLs
12. **End-to-end test** — Upload ZIP → see it process → view splat

### Phase 4: Frontend Polish
13. **Job progress page** — Real-time Firestore listener + progress UI
14. **Splat viewer page** — Integrate `gaussian-splats-3d` with signed URL
15. **Job list / dashboard** — Show all jobs with status
16. **Error states** — Retry button, helpful error messages

### Phase 5: Hardening
17. **Auth** (optional) — Firebase Auth for user accounts
18. **Rate limiting** — Max concurrent jobs, monthly budget cap
19. **Pod failure recovery** — Automatic retry on transient failures
20. **Monitoring** — Cloud Function logs, error alerting

---

## Quick Reference

| What | Where |
|------|-------|
| Frontend | SvelteKit on Firebase Hosting |
| Database | Firestore (`jobs/` collection) |
| File storage | Firebase Cloud Storage (`uploads/`, `results/`) |
| Orchestration | Cloud Functions (Firestore triggers + scheduled) |
| GPU compute | RunPod community cloud (RTX A5000) |
| Worker image | Docker on Docker Hub |
| Splat viewer | `@mkkellogg/gaussian-splats-3d` |
| Pipeline code | `arkit_to_colmap.py` → COLMAP → gsplat → `ply_to_splat.py` |
| Cost per job | ~$0.13 |
| Time per job | ~50 minutes |
| Raw pipeline docs | [PIPELINE.md](./PIPELINE.md) |
