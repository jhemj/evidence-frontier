"""Full-envelope estimates and measured token receipts; never chars==tokens."""
import math


def envelope(messages,context,output,*,serialized_request=None,template_sha256=None):
    text=''.join(m['content'] for m in messages)
    # Every system/user message already includes the generation schema once.
    # The protocol format/schema is also recorded in the exact wire envelope,
    # but its grammar side-channel is not known to be input-context tokens.
    # Do not count JSON escaping/options or a second schema copy as known model
    # tokens. The 512 reserve is only a heuristic for an unknown chat template.
    counted=text
    wire=serialized_request if serialized_request is not None else text
    ascii_count=sum(ord(c)<128 for c in counted)
    # Advisory, deliberately padded multilingual estimate. It is NOT a model
    # tokenizer result or proof that the remote server retained every token.
    estimate=math.ceil(ascii_count/3+(len(counted)-ascii_count)*1.5)+512
    return {'prompt_characters':len(text),'prompt_utf8_bytes':len(text.encode()),
        'estimated_input_tokens':estimate,'estimate_basis':'padded multilingual heuristic, not exact tokenizer',
        'count_basis':'heuristic','count_scope':'full_messages_with_embedded_schema_and_template_reserve',
        'request_characters':len(wire),'request_utf8_bytes':len(wire.encode()),
        'protocol_schema_cost_basis':'Unknown side-channel grammar cost; schema is already included in messages, not double-counted as input tokens.',
        'tokenizer_version':None,'server_template_version':None,'template_sha256':template_sha256,
        'context_tokens_requested':context,'output_tokens_reserved':output,
        'estimated_headroom':context-output-estimate,'exact_input_tokens':None,
        'server_retained_full_input_verified':False}


def measured(budget,usage):
    result=dict(budget)
    count=usage.get('prompt_eval_count',usage.get('prompt_tokens'))
    generated=usage.get('eval_count',usage.get('completion_tokens'))
    result.update(exact_input_tokens=count,actual_output_tokens=generated,
        actual_headroom=budget['context_tokens_requested']-count-budget['output_tokens_reserved'] if type(count) is int else None)
    # A provider counter is a count of evaluated tokens, not evidence that an
    # oversized upstream request was never truncated before evaluation.
    return result
