import { describe, expect, it } from 'vitest';

import { datasetPath } from './dataset';

describe('datasetPath', () => {
  it('rewrites a canonical export directory to a site-relative one', () => {
    expect(datasetPath('s3://some-export-bucket/canonical/run_aws_canonical/')).toBe(
      '/canonical/run_aws_canonical/',
    );
  });

  it('offers no link for an export the distribution does not serve', () => {
    // Rehearsals live under a different prefix and are not published. A link here
    // would be an invitation to a 404 -- or, worse, a claim that a staging run's
    // output is the released dataset.
    expect(datasetPath('s3://some-export-bucket/runs/run_aws_staging/export-1/')).toBeNull();
    expect(datasetPath('s3://some-export-bucket/canonical/run/nested/')).toBeNull();
    expect(datasetPath('/tmp/local/dataset')).toBeNull();
    expect(datasetPath('')).toBeNull();
  });
});
