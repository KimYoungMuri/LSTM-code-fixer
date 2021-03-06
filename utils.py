import re
import torch
import torch.nn.utils.rnn as rnn_utils
from torch.autograd import Variable

PAD = '<PAD>'
PAD_INDEX = 0
UNK = '<UNK>'
UNK_INDEX = 1
BOS = '<BOS>'
BOS_INDEX = 2
EOS = '<EOS>'
EOS_INDEX = 3

_GPUS_EXIST = True


def try_gpu(x):
    """Try to put x on a GPU."""
    global _GPUS_EXIST
    if _GPUS_EXIST:
        try:
            return x.cuda()
        except (AssertionError, RuntimeError):
            print('No GPUs detected. Sticking with CPUs.')
            _GPUS_EXIST = False
    return x


class Stats(object):
    def __init__(self):
        self.n = 0
        self.n_batches = 0
        self.loss_localize = 0.
        self.loss_edit = 0.
        self.accuracy_localize = 0.
        self.accuracy_edit1 = 0.
        self.accuracy_edit2 = 0.
        self.accuracy_repair = 0.
        self.grad_norm = 0.

    def add(self, stats):
        self.n += stats.n
        self.n_batches += stats.n_batches
        self.loss_localize += stats.loss_localize
        self.loss_edit += stats.loss_edit
        self.accuracy_localize += stats.accuracy_localize
        self.accuracy_edit1 += stats.accuracy_edit1
        self.accuracy_edit2 += stats.accuracy_edit2
        self.accuracy_repair += stats.accuracy_repair
        self.grad_norm = max(self.grad_norm, stats.grad_norm)

    def __repr__(self):
        n = max(1, self.n) * 1.
        n_batches = max(1, self.n_batches) * 1.
        return '(n={}, loss_localize={:.6f}, loss_edit={:.6f}, acc_localize={:.2f}%, acc_edit1={:.2f}%, acc_edit2={:.2f}%, acc_repair={:.2f}%, grad_norm={:.6f})'.format(
            self.n,
            self.loss_localize / n_batches,
            self.loss_edit / n_batches,
            self.accuracy_localize / n * 100,
            self.accuracy_edit1 / n * 100,
            self.accuracy_edit2 / n * 100,
            self.accuracy_repair / n * 100,
            self.grad_norm,
        )

    __str__ = __repr__

    def log(self, tb_logger, step, prefix='', ignore_grad_norm=False):
        n = float(self.n)
        n_batches = float(self.n_batches)
        tb_logger.add_scalar(prefix + 'loss_localize', self.loss_localize / n_batches, step)
        tb_logger.add_scalar(prefix + 'loss_edit', self.loss_edit / n_batches, step)
        tb_logger.add_scalar(prefix + 'accuracy_localize', self.accuracy_localize / n * 100, step)
        tb_logger.add_scalar(prefix + 'accuracy_edit1', self.accuracy_edit1 / n * 100, step)
        tb_logger.add_scalar(prefix + 'accuracy_edit2', self.accuracy_edit2 / n * 100, step)
        tb_logger.add_scalar(prefix + 'accuracy_repair', self.accuracy_repair / n * 100, step)
        if not ignore_grad_norm:
            tb_logger.add_scalar(prefix + 'grad_norm', self.grad_norm, step)


def batch_iter(iterable, batch_size, sort_items_by=None):
    batch = []
    for x in iterable:
        batch.append(x)
        if len(batch) == batch_size:
            if sort_items_by is not None:
                batch.sort(key=sort_items_by)
            yield batch
            batch = []
    if batch:
        if sort_items_by is not None:
            batch.sort(key=sort_items_by)
        yield batch


TEXT_TOKENIZER = re.compile(r'\w+|[^\w\s]', re.UNICODE)


def tokenize_err_msg(text):
    return TEXT_TOKENIZER.findall(text)


def prepare_rnn_seq(rnn_input, lengths, hx=None, masks=None, batch_first=False):
    def check_decreasing(lengths):
        lens, order = torch.sort(lengths, dim=0, descending=True)
        if torch.ne(lens, lengths).sum() == 0:
            return None
        else:
            _, rev_order = torch.sort(order)
            return lens, Variable(order), Variable(rev_order)

    check_res = check_decreasing(lengths)

    if check_res is None:
        lens = lengths
        rev_order = None
    else:
        lens, order, rev_order = check_res
        batch_dim = 0 if batch_first else 1
        rnn_input = rnn_input.index_select(batch_dim, order)
        if hx is not None:
            # hack lstm
            if isinstance(hx, tuple):
                hx, cx = hx
                hx = hx.index_select(1, order)
                cx = cx.index_select(1, order)
                hx = (hx, cx)
            else:
                hx = hx.index_select(1, order)

    lens = lens.tolist()
    seq = rnn_utils.pack_padded_sequence(rnn_input, lens, batch_first=batch_first)
    if masks is not None:
        if batch_first:
            masks = masks[:, :lens[0]]
        else:
            masks = masks[:lens[0]]
    return seq, hx, rev_order, masks


def recover_rnn_seq(seq, rev_order, hx=None, batch_first=False):
    output, _ = rnn_utils.pad_packed_sequence(seq, batch_first=batch_first)
    if rev_order is not None:
        batch_dim = 0 if batch_first else 1
        output = output.index_select(batch_dim, rev_order)
        if hx is not None:
            # hack lstm
            if isinstance(hx, tuple):
                hx, cx = hx
                hx = hx.index_select(1, rev_order)
                cx = cx.index_select(1, rev_order)
                hx = (hx, cx)
            else:
                hx = hx.index_select(1, rev_order)
    return output, hx
