"""System prompts for the conversational turn and the performance classifier.

Two distinct prompts. The conversational prompt must not ask Claude for stage
directions -- no "[smiles thoughtfully]" leaking into spoken text (build plan
6). The classifier prompt is small, heavily constrained and emits only the
structured performance description.

TODO: write.
"""
