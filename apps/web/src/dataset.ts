/**
 * Where a published dataset can be fetched from, relative to this site.
 *
 * An export manifest records where it was written as an `s3://` URL, which is a fact
 * about the bucket and not something a reader can click. The distribution serves the
 * canonical prefix of that bucket and only that prefix, so the mapping is a rewrite of
 * one known shape and a refusal for anything else: a non-canonical export is a
 * rehearsal, it is not published, and offering a link to it would 404 at best.
 */
const CANONICAL_DIRECTORY = /^s3:\/\/[^/]+\/(canonical\/[^/]+\/)$/;

/** The site-relative directory this export can be downloaded from, or null. */
export function datasetPath(directory: string): string | null {
  const match = CANONICAL_DIRECTORY.exec(directory.trim());
  return match ? `/${match[1]}` : null;
}
