# ADR-001: Study application-level memory, not the model's internal state

Status: accepted, 2026-08-29.

## Context

The interesting question is what a mind becomes when it is forced to forget. There
are two places that question could be asked of a language model.

The first is inside the model: the KV cache, the attention distribution, the hidden
state. Work on attention sinks and cache eviction lives there. It is also
inaccessible to us. Amazon Bedrock exposes an inference API, not a cache handle;
even with an open-weights model, an experiment at that level would be measuring
kernel-level artefacts rather than anything an application could act on, and would
tempt us towards claims about the model's experience that no evidence here could
support.

The second is outside the model: the explicit record of episodes the application
chooses to place in the prompt. This is where every production agent's memory
actually lives, and where every production engineer actually has a decision to make.

## Decision

The experiment operates exclusively on explicit external memory records supplied to
the model by the application. Six arms share seed memories, stimuli, writer model,
prompts, inference settings, and token budget, and differ only in the policy that
decides which records survive.

We make no claim, anywhere in the code, the API, the documentation, or the public
interface, about modifying or inspecting the model's KV cache, attention matrices,
hidden state, or consciousness. The name "Attention Sink" is an allusion to that
literature, not an assertion of mechanism.

## Consequences

- The mechanism under study is one a reader can implement in their own system the
  same afternoon. That is the point.
- Every arm is a deterministic function of logged state, because the policy engine
  is ordinary code operating on ordinary records.
- The token budget is denominated in an explicit, versioned unit of our own rather
  than in a vendor's tokenisation. It is applied identically to every arm, which is
  what the comparison requires; it is not a claim about context-window occupancy.
- Results speak to application-level memory design. They do not transfer to
  KV-cache eviction, and any write-up must say so.

## Revisit when

An inference provider exposes a supported, observable interface to cache retention,
and a second experiment could be run at that level as a genuine comparison rather
than as a stronger-sounding version of this one.
