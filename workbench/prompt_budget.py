"""Full-envelope estimates and measured token receipts; never chars==tokens."""
import math


def envelope(messages,context,output):
    text=''.join(m['content'] for m in messages)
    ascii_count=sum(ord(c)<128 for c in text)
    # Advisory, deliberately padded multilingual estimate. It is NOT a model
    # tokenizer result or proof that the remote server retained every token.
    estimate=math.ceil(ascii_count/3+(len(text)-ascii_count)*1.5)+512
    return {'prompt_characters':len(text),'prompt_utf8_bytes':len(text.encode()),
        'estimated_input_tokens':estimate,'estimate_basis':'padded multilingual heuristic, not exact tokenizer',
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
