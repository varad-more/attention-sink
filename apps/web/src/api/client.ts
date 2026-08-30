/**
 * The only place this application talks to the API.
 *
 * Everything the exhibition renders comes through here. No component imports
 * experiment JSON, and no component holds a fallback dataset: a view with no data is
 * a view that says so, because a silent fallback is how a blank exhibition starts
 * looking like a real one.
 *
 * Immutable records -- a committed cycle, a stored interview -- are cached forever
 * once fetched. The server marks them `immutable` and gives them an ETag; a cycle
 * that has been committed cannot change, so re-fetching it while scrubbing a
 * timeline is pure waste.
 */

import type { AppConfig } from '../config';
import type {
  ApiEnvelope,
  ArmSummary,
  ContradictionRow,
  CycleView,
  DivergenceMatrices,
  EchoRow,
  ExportManifest,
  GraveyardView,
  InterviewView,
  Lineage,
  MetricRow,
  Page,
  QuestionScoreRow,
  RunSummary,
} from './types';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly url: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }

  /** Whether the resource is absent rather than the API being unreachable. */
  get isNotFound(): boolean {
    return this.status === 404;
  }
}

/** Reads the API. One instance per client; holds the immutable-record cache. */
export class ApiClient {
  private readonly immutable = new Map<string, unknown>();

  constructor(private readonly config: AppConfig) {}

  private get runPath(): string {
    return `${this.config.apiBaseUrl}/runs/${encodeURIComponent(this.config.runId)}`;
  }

  private async get<T>(url: string, options: { cache?: boolean } = {}): Promise<T> {
    if (options.cache && this.immutable.has(url)) {
      return this.immutable.get(url) as T;
    }
    let response: Response;
    try {
      response = await fetch(url, { headers: { accept: 'application/json' } });
    } catch {
      throw new ApiError(
        `cannot reach the local API at ${this.config.apiBaseUrl}. Is \`make local-api\` running?`,
        0,
        url,
      );
    }
    if (!response.ok) {
      throw new ApiError(`${response.status} from ${url}`, response.status, url);
    }
    const body = (await response.json()) as ApiEnvelope<T>;
    if (options.cache) this.immutable.set(url, body.data);
    return body.data;
  }

  health(): Promise<{ status: string }> {
    return this.get(`${this.config.apiBaseUrl}/health`);
  }

  run(): Promise<RunSummary> {
    return this.get(this.runPath);
  }

  arms(): Promise<ArmSummary[]> {
    return this.get(`${this.runPath}/arms`);
  }

  arm(armId: string): Promise<ArmSummary> {
    return this.get(`${this.runPath}/arms/${encodeURIComponent(armId)}`);
  }

  completedCycles(limit = 200): Promise<Page<number>> {
    return this.get(`${this.runPath}/cycles?limit=${limit}`);
  }

  /** One committed cycle across all six arms. Cached: a committed cycle is immutable. */
  cycle(cycle: number): Promise<CycleView[]> {
    return this.get(`${this.runPath}/cycles/${cycle}`, { cache: true });
  }

  graveyard(
    params: { armId?: string; limit?: number; offset?: number } = {},
  ): Promise<Page<GraveyardView>> {
    const query = new URLSearchParams();
    if (params.armId) query.set('arm_id', params.armId);
    query.set('limit', String(params.limit ?? 200));
    query.set('offset', String(params.offset ?? 0));
    return this.get(`${this.runPath}/graveyard?${query.toString()}`);
  }

  graveyardEntry(memoryId: string): Promise<GraveyardView> {
    return this.get(`${this.runPath}/graveyard/${encodeURIComponent(memoryId)}`, { cache: true });
  }

  interviews(): Promise<InterviewView[]> {
    return this.get(`${this.runPath}/interviews`);
  }

  interviewsAt(cycle: number): Promise<InterviewView[]> {
    return this.get(`${this.runPath}/interviews/${cycle}`, { cache: true });
  }

  metrics(params: { metricName?: string; armId?: string } = {}): Promise<Page<MetricRow>> {
    const query = new URLSearchParams();
    if (params.metricName) query.set('metric_name', params.metricName);
    if (params.armId) query.set('arm_id', params.armId);
    query.set('limit', '200');
    return this.get(`${this.runPath}/metrics?${query.toString()}`);
  }

  echoes(params: { armId?: string } = {}): Promise<Page<EchoRow>> {
    const query = new URLSearchParams();
    if (params.armId) query.set('arm_id', params.armId);
    query.set('limit', '200');
    return this.get(`${this.runPath}/echoes?${query.toString()}`);
  }

  contradictions(cycle?: number): Promise<Page<ContradictionRow>> {
    const query = new URLSearchParams({ limit: '200' });
    if (cycle !== undefined) query.set('cycle', String(cycle));
    return this.get(`${this.runPath}/contradictions?${query.toString()}`);
  }

  questionScores(): Promise<QuestionScoreRow[]> {
    return this.get(`${this.runPath}/question-scores`);
  }

  divergence(): Promise<DivergenceMatrices> {
    return this.get(`${this.runPath}/divergence`);
  }

  lineage(memoryId: string): Promise<Lineage> {
    return this.get(`${this.runPath}/lineage/${encodeURIComponent(memoryId)}`, { cache: true });
  }

  exports(): Promise<ExportManifest[]> {
    return this.get(`${this.runPath}/exports`);
  }
}
