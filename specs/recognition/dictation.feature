@transformer
Feature: Dictation with a transformer model

  A transformer mode transcribes free speech. Because these models work on whole utterances
  rather than streaming, the mode has to decide for itself where an utterance begins and ends.
  It does that with an energy gate: audio above a threshold starts a recording, and a stretch of
  quiet ends it.

  A pre-roll buffer keeps the moment before the gate opened, so the first syllable is not lost.

  Background:
    Given the configuration:
      """
      {
        "modes": {
          "dictate": {
            "type": "transformer",
            "model_name": "whisper-turbo",
            "silence_seconds": 0.3,
            "max_chunk_seconds": 30.0,
            "energy_threshold": 0.01,
            "pre_roll_seconds": 0.25,
            "lang": "en",
            "commands": { "{rest}": "write({1})" }
          },
          "root mode": { "type": "vosk", "path": "models/small-en" }
        }
      }
      """
    And the app is running
    And the active mode is "dictate"

  Rule: Recording starts when audio exceeds the energy threshold

    Scenario: Quiet audio starts nothing
      When 5 seconds of audio below the energy threshold arrive
      Then no transcription is attempted

    Scenario: Audio above the threshold starts a recording
      When audio above the energy threshold arrives
      Then a recording is in progress

  Rule: The pre-roll preserves speech from before the gate opened

    Scenario: Audio buffered before the threshold is included in the chunk
      When 1 second of audio below the energy threshold arrives
      And audio above the energy threshold arrives
      And 0.3 seconds of silence pass
      Then the transcribed chunk begins 0.25 seconds before the threshold was crossed

    Scenario: The pre-roll is bounded by pre_roll_seconds
      When 10 seconds of audio below the energy threshold arrive
      And audio above the energy threshold arrives
      And 0.3 seconds of silence pass
      Then the transcribed chunk contains at most 0.25 seconds of pre-threshold audio

  Rule: A chunk ends on silence or at the maximum length

    Scenario: Silence flushes the chunk
      Given a recording is in progress
      When 0.3 seconds of silence pass
      Then the chunk is transcribed
      And the recording ends

    Scenario: Silence shorter than the threshold does not flush
      Given a recording is in progress
      When 0.2 seconds of silence pass
      And audio above the energy threshold arrives
      Then no transcription is attempted
      And a recording is in progress

    Scenario: A pause mid-sentence keeps one utterance together
      Given a recording is in progress
      When 0.2 seconds of silence pass
      And 1 second of audio above the energy threshold arrives
      And 0.3 seconds of silence pass
      Then exactly one chunk is transcribed

    Scenario: Continuous speech is cut at the maximum chunk length
      Given a recording is in progress
      When 30 seconds of audio above the energy threshold arrive
      Then the chunk is transcribed
      And a new recording begins immediately

    Scenario: An empty chunk is not sent to the model
      When a chunk containing no samples is flushed
      Then no transcription is attempted

  Rule: Transcribed text goes through normal command dispatch

    A dictation mode types text because its config maps {rest} to a write action, not because
    dictation bypasses dispatch. Reserved phrases and mode names are still honoured.

    Scenario: Transcribed prose is typed
      When the model transcribes "hello world"
      Then the text "hello world" is typed

    Scenario: Leading and trailing whitespace is trimmed
      When the model transcribes "  hello world  "
      Then the text "hello world" is typed

    Scenario: An empty transcription produces nothing
      When the model transcribes ""
      Then no input is emitted

    Scenario: A reserved phrase in transcribed text is acted on, not typed
      When the model transcribes "reload config"
      Then the reload is reported as applied
      And no text is typed

  Rule: Disabling a mode discards its in-flight state

    Scenario: A partial recording is abandoned when the mode is switched away
      # closes M5 — blocks are dropped while disabled but recording/current_chunk are not reset,
      # so re-enabling stitches audio across the gap into one nonsensical utterance
      Given a recording is in progress
      When I say "root mode"
      Then the recording is discarded
      And no transcription is attempted

    Scenario: Re-enabling starts a fresh recording
      # closes M5
      Given a recording is in progress
      When I say "root mode"
      And I say "dictate"
      And audio above the energy threshold arrives
      And 0.3 seconds of silence pass
      Then the transcribed chunk contains only audio from after the mode became active

    Scenario: Audio arriving while disabled is not queued for later
      Given the active mode is "root mode"
      When 10 seconds of audio above the energy threshold arrive
      And I say "dictate"
      Then no transcription is attempted

  Rule: The session transcript is not accumulated indefinitely

    Scenario: Transcribed text is not retained after it has been dispatched
      # closes M5 — self.text grows for the entire process lifetime today and is only read by
      # an end() method that is never called
      When the model transcribes "hello world"
      And the model transcribes "goodbye world"
      Then no session transcript is retained

  Rule: Both model backends behave the same from the outside

    The model_name selects a backend: a "whisper-" prefix loads openai-whisper, anything else is
    treated as a HuggingFace model id. Callers must not be able to tell which was used.

    Scenario Outline: Backend selection
      Given a transformer mode with model_name "<model_name>"
      Then the "<backend>" backend is loaded
      And the model is asked for "<resolved>"

      Examples:
        | model_name              | backend         | resolved                |
        | whisper-turbo           | openai-whisper  | turbo                   |
        | whisper-base            | openai-whisper  | base                    |
        | openai/whisper-large-v3 | transformers    | openai/whisper-large-v3 |

    Scenario: Both backends return plain text
      # closes C6 — the whisper path returns a dict, the transformers path returns whatever
      # decode() gives back, and the caller branches on a list type neither produces
      Given a transformer mode with model_name "openai/whisper-large-v3"
      When the model transcribes "hello world"
      Then the transcription result is the string "hello world"

    Scenario: The configured device is honoured
      # closes C6 — transformer_device is accepted in config and never used; device_map="auto"
      # is hardcoded
      Given a transformer mode with model_name "openai/whisper-large-v3" and transformer_device "cpu"
      When the model is loaded
      Then the model is placed on device "cpu"

    Scenario: The configured language reaches the model
      # closes C6 — language is passed to the feature extractor, which does not accept it
      Given a transformer mode with model_name "openai/whisper-large-v3" and lang "cs"
      When a chunk is transcribed
      Then the model is asked to decode in language "cs"

  Rule: A model already on disk is loaded without contacting the Hub

    Startup runs before the user can say anything, so it must not wait on the network. Left to
    itself, the HuggingFace backend revalidates every file of the repo against huggingface.co on
    each start -- config, generation config, processor, tokenizer and weight index, a round trip
    apiece -- which is seconds spent confirming what is already cached, and an unbounded wait on
    a slow connection. The cache is authoritative; the Hub is asked only for a model that is
    genuinely missing.

    Scenario: A cached HuggingFace model starts without a network round trip
      Given mode "dictate" names a HuggingFace model that is in the local cache
      When the app starts
      Then the model is loaded from the cache
      And no request is made to huggingface.co

    Scenario: A cached model still starts with the network unplugged
      Given mode "dictate" names a HuggingFace model that is in the local cache
      And the machine has no network connection
      When the app starts
      Then mode "dictate" transcribes speech normally

    Scenario: A model that is not cached yet is fetched, and the wait is announced
      Given mode "dictate" names a HuggingFace model that is not in the local cache
      When the app starts
      Then the download is logged naming the model before it begins
      And the model is fetched from huggingface.co

  Rule: A model that cannot be loaded degrades the mode, not the app

    @accessibility-critical
    Scenario: An unavailable model leaves other modes working
      Given mode "dictate" names a model that cannot be loaded
      When the app starts
      Then an error is logged naming mode "dictate"
      And the tray icon shows the fault colour
      And mode "root mode" recognizes commands normally

    @accessibility-critical
    Scenario: Switching to a failed mode is refused
      Given mode "dictate" failed to load its model
      And the active mode is "root mode"
      When I say "dictate"
      Then the active mode is still "root mode"
      And a warning is logged explaining that mode "dictate" is unavailable
