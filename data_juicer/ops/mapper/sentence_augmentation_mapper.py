import logging

from data_juicer.ops.base_op import OPERATORS, Mapper
from data_juicer.utils.lazy_loader import LazyLoader
from data_juicer.utils.model_utils import get_model, prepare_model

torch = LazyLoader("torch")
transformers = LazyLoader("transformers")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

DEFAULT_SYSTEM_PROMPT = "A chat between a curious user and an artificial \
    intelligence assistant. The assistant gives helpful, detailed, and \
        polite answers to the user's questions."

OP_NAME = "sentence_augmentation_mapper"


@OPERATORS.register_module(OP_NAME)
class SentenceAugmentationMapper(Mapper):
    """Mapper to augment sentences.
    The purpose of this operation is to enhance sentences.
    If the input text is at the document level, the enhancement
    effect may not be optimal. Therefore, please consider the
    length of the input text carefully.

    Recommended model list: [
        lmsys/vicuna-13b-v1.5
        Qwen/Qwen2-7B-Instruct
    ]
    """

    _accelerator = "cuda"

    def __init__(
        self,
        hf_model: str = "Qwen/Qwen2-7B-Instruct",
        system_prompt: str = None,
        task_sentence: str = None,
        max_new_tokens=256,
        temperature=0.2,
        top_p=None,
        num_beams=1,
        text_key=None,
        text_key_second=None,
        *args,
        **kwargs,
    ):
        """
        Initialization method.
        :param hf_model: Huggingface model id.
        :param system_prompt: System prompt.
        :param task_sentence: The instruction for the current task.
        :param max_new_tokens: the maximum number of new tokens
            generated by the model.
        :param temperature: used to control the randomness of
            generated text. The higher the temperature, the more
            random and creative the generated text will be.
        :param top_p: randomly select the next word from the group
            of words whose cumulative probability reaches p.
        :param num_beams: the larger the beam search size, the higher
            the quality of the generated text.
        :param text_key: the key name used to store the first sentence
            in the text pair. (optional, defalut='text')
        :param text_key_second: the key name used to store the second sentence
            in the text pair.
        :param args: extra args
        :param kwargs: extra args
        """
        kwargs.setdefault("mem_required", "31GB")
        kwargs.setdefault("num_proc", 1)
        super().__init__(*args, **kwargs)

        if system_prompt is None:
            system_prompt = DEFAULT_SYSTEM_PROMPT
        self.system_prompt = system_prompt
        self.hf_model = hf_model
        self.max_new_tokens = max_new_tokens

        self.model_key = prepare_model(model_type="huggingface", pretrained_model_name_or_path=hf_model)
        self.temperature = temperature
        self.top_p = top_p
        self.num_beams = num_beams
        self.task_sentence = task_sentence
        self.text_key_second = text_key_second

        if text_key is not None:
            self.text_key = text_key

    def process_single(self, sample=None, rank=None):
        # there is no target text
        if self.text_key_second is None:
            logger.error(
                "This OP (text_pair_similarity_filter) requires \
                processing multiple fields, and you need to specify \
                valid `text_key_second`"
            )

        if self.task_sentence is None:
            print("[Warning] task_sentence is None!")
            sample[self.text_key] = ""
            sample[self.text_key_second] = ""
            return sample

        model, processor = get_model(model_key=self.model_key, rank=rank, use_cuda=self.use_cuda())

        if "vicuna" in self.hf_model:
            input_prompt = (
                self.system_prompt
                + ' USER: Here \
                is a sentence: "'
                + sample[self.text_key]
                + '". '
                + self.task_sentence
                + " ASSISTANT:"
            )

        else:
            messages = [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": 'Here is a sentence: "' + sample[self.text_key] + '". ' + self.task_sentence,
                },
            ]
            input_prompt = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

        inputs = processor(input_prompt, return_tensors="pt").to(model.device)
        response = model.generate(
            **inputs,
            max_new_tokens=self.max_new_tokens,
            eos_token_id=processor.eos_token_id,
            top_p=self.top_p,
            temperature=self.temperature,
            num_beams=self.num_beams,
        )
        input_token_len = inputs.input_ids.shape[1]
        n_diff_input_output = (inputs.input_ids != response[:, :input_token_len]).sum().item()
        if n_diff_input_output > 0:
            print(
                f"[Warning] {n_diff_input_output} output_ids are \
                    not the same as the input_ids"
            )
        output = processor.batch_decode(response[:, input_token_len:], skip_special_tokens=True)[0]
        output = output.strip().strip('"')

        sample[self.text_key_second] = output

        return sample
