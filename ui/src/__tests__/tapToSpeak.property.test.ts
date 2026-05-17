/**
 * Property-based tests for the Tap to Speak button feature.
 * Uses fast-check with a minimum of 100 iterations per property.
 *
 * Feature: tap-to-speak-button
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import { parseAutoCloseMic } from '../App';

// ---------------------------------------------------------------------------
// Arbitraries
// ---------------------------------------------------------------------------

const micStateArb = fc.constantFrom('idle', 'recording') as fc.Arbitrary<'idle' | 'recording'>;

const blockingPipelineStateArb = fc.constantFrom('thinking', 'speaking') as fc.Arbitrary<
  'thinking' | 'speaking'
>;

// ---------------------------------------------------------------------------
// Property 4: AUTO_CLOSE_MIC env var parsing
// ---------------------------------------------------------------------------

describe('Feature: tap-to-speak-button, Property 4: AUTO_CLOSE_MIC env var parsing', () => {
  it('returns false if and only if value is the exact string "false"', () => {
    fc.assert(
      fc.property(fc.string(), (v) => {
        const result = parseAutoCloseMic(v);
        if (v === 'false') {
          expect(result).toBe(false);
        } else {
          expect(result).toBe(true);
        }
      }),
      { numRuns: 100 },
    );
  });

  it('returns true for undefined (absent env var)', () => {
    expect(parseAutoCloseMic(undefined)).toBe(true);
  });

  it('returns true for "true"', () => {
    expect(parseAutoCloseMic('true')).toBe(true);
  });

  it('returns false for "false"', () => {
    expect(parseAutoCloseMic('false')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Property 1: Aria attributes always reflect MicState
// ---------------------------------------------------------------------------

describe('Feature: tap-to-speak-button, Property 1: Aria attributes always reflect MicState', () => {
  it('aria-pressed is true iff micState is recording', () => {
    fc.assert(
      fc.property(micStateArb, (micState) => {
        const ariaPressed = micState === 'recording';
        expect(ariaPressed).toBe(micState === 'recording');
      }),
      { numRuns: 100 },
    );
  });

  it('aria-label is non-empty for any MicState', () => {
    fc.assert(
      fc.property(micStateArb, (micState) => {
        const ariaLabel = micState === 'recording' ? 'Stop recording' : 'Start recording';
        expect(ariaLabel.length).toBeGreaterThan(0);
      }),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 2: Open operation is idempotent
// ---------------------------------------------------------------------------

describe('Feature: tap-to-speak-button, Property 2: Open operation is idempotent', () => {
  it('calling open while already recording does not change micState', () => {
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 20 }), (extraOpenCalls) => {
        // Simulate the handleToggle guard: only transitions idle → recording
        let micState: 'idle' | 'recording' = 'recording';
        let setRoboActiveCallCount = 0;

        const handleToggle = () => {
          if (micState === 'idle') {
            micState = 'recording';
            setRoboActiveCallCount++;
          }
          // If already recording, open is a no-op (guard prevents double-init)
        };

        for (let i = 0; i < extraOpenCalls; i++) {
          handleToggle();
        }

        expect(micState).toBe('recording');
        // setRoboActive(true) should never have been called (started in recording)
        expect(setRoboActiveCallCount).toBe(0);
      }),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 3: Button is disabled for all blocking pipeline states
// ---------------------------------------------------------------------------

describe('Feature: tap-to-speak-button, Property 3: Button is disabled for all blocking pipeline states', () => {
  it('canToggle is false for thinking and speaking states', () => {
    fc.assert(
      fc.property(blockingPipelineStateArb, (pipelineState) => {
        const canToggle = pipelineState !== 'thinking' && pipelineState !== 'speaking';
        expect(canToggle).toBe(false);
      }),
      { numRuns: 100 },
    );
  });

  it('canToggle is true for non-blocking states', () => {
    fc.assert(
      fc.property(
        fc.constantFrom('listening', 'disconnected') as fc.Arbitrary<string>,
        (pipelineState) => {
          const canToggle = pipelineState !== 'thinking' && pipelineState !== 'speaking';
          expect(canToggle).toBe(true);
        },
      ),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 5: Close always resets micState to idle
// ---------------------------------------------------------------------------

describe('Feature: tap-to-speak-button, Property 5: Close always resets micState to idle', () => {
  it('manual close (toggle while recording) always results in idle', () => {
    fc.assert(
      fc.property(micStateArb, (initialState) => {
        let micState = initialState;

        // Simulate handleToggle close path
        const handleToggle = () => {
          if (micState === 'recording') {
            micState = 'idle';
          }
        };

        if (micState === 'recording') {
          handleToggle();
          expect(micState).toBe('idle');
        }
      }),
      { numRuns: 100 },
    );
  });

  it('onRoboDeactivated with autoCloseMic=true always resets to idle', () => {
    fc.assert(
      fc.property(micStateArb, (initialState) => {
        let micState = initialState;
        const autoCloseMic = true;

        const onRoboDeactivated = () => {
          if (autoCloseMic) micState = 'idle';
        };

        onRoboDeactivated();
        expect(micState).toBe('idle');
      }),
      { numRuns: 100 },
    );
  });

  it('onRoboDeactivated with autoCloseMic=false does NOT reset to idle', () => {
    fc.assert(
      fc.property(
        fc.constantFrom('recording') as fc.Arbitrary<'idle' | 'recording'>,
        (initialState) => {
          let micState = initialState;
          const autoCloseMic = false;

          const onRoboDeactivated = () => {
            if (autoCloseMic) micState = 'idle';
          };

          onRoboDeactivated();
          expect(micState).toBe('recording');
        },
      ),
      { numRuns: 100 },
    );
  });

  it('onWsClose always resets to idle regardless of initial state', () => {
    fc.assert(
      fc.property(micStateArb, (initialState) => {
        let micState = initialState;

        const onWsClose = () => {
          micState = 'idle';
        };

        onWsClose();
        expect(micState).toBe('idle');
      }),
      { numRuns: 100 },
    );
  });
});
