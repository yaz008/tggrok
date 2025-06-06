from asyncio import run_coroutine_threadsafe, set_event_loop
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from queue import Empty, Queue
from re import match
from threading import Event, Thread
from typing import cast, ClassVar

from pyrogram.client import Client
from pyrogram.enums import ParseMode
from pyrogram.filters import user
from pyrogram.types import Message


def get_version(package_name: str, default_version: str = '0.0.0') -> str:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return default_version


@dataclass(slots=True)
class Grok:
    api_id: str
    api_hash: str
    phone_number: str
    parse_mode: ParseMode = ParseMode.DEFAULT
    workdir: str = str(Path.cwd())
    hide_password: bool = True
    timeout: float | None = None
    __response_queue: Queue[str] = field(init=False)
    __tg: Client = field(init=False)
    __tg_thread: Thread = field(init=False)
    __ready_event: Event = field(init=False)
    __is_created: ClassVar[bool] = field(default=False, init=False)

    def __post_init__(self) -> None:
        assert match(r'^[\d]+$', self.api_id)
        assert match(r'^[0-9a-f]{32}$', self.api_hash)
        assert match(r'^\+[\d]{10,12}$', self.phone_number)
        if Grok.__is_created:
            raise RuntimeError(f'{Grok.__name__} is already instantiated')
        Grok.__is_created = True
        self.__response_queue = Queue(maxsize=1)
        self.__tg = Client(
            name='GrokAPI',
            api_id=self.api_id,
            api_hash=self.api_hash,
            phone_number=self.phone_number,
            app_version=f'GrokAI-{get_version('tggrok')}',
            device_model=f'PythonGrokAPI-{get_version('tggrok')}',
            system_version=f'GrokAPI-{get_version('tggrok')}',
            workdir=str(self.workdir),
            parse_mode=self.parse_mode,
            hide_password=self.hide_password,
        )
        self.__tg.on_message(user('@GrokAI'))(self.__on_response)
        self.__ready_event = Event()
        self.__tg_thread = Thread(target=self.__run, daemon=True)
        self.__tg_thread.start()
        if not self.__ready_event.wait(self.timeout):
            raise TimeoutError(
                f'Telegram connection timed out after {self.timeout} seconds'
            )

    def __run(self) -> None:
        set_event_loop(self.__tg.loop)
        self.__tg.start()  # type: ignore[unused-coroutine]
        self.__ready_event.set()
        self.__tg.loop.run_forever()

    def __on_response(self, _: Client, response: Message) -> None:
        if self.__response_queue.empty():
            self.__response_queue.put_nowait(response.text)

    def ask[T = str](  # noqa: E251
        self,
        prompt: str,
        *,
        process: Callable[[str], T] | None = None,
        timeout: float | None = None,
        mark_as_read: bool = True,
        keep_context: bool = True,
    ) -> T:
        run_coroutine_threadsafe(
            coro=self.__tg.send_message(chat_id='@GrokAI', text=prompt),
            loop=self.__tg.loop,
        )
        try:
            response: str = self.__response_queue.get(timeout=timeout)
            result: T = process(response) if process is not None else cast(T, response)
            if not keep_context:
                self.reset_dialog()
            if mark_as_read:
                run_coroutine_threadsafe(
                    coro=self.__tg.read_chat_history(chat_id='@GrokAI'),
                    loop=self.__tg.loop,
                ).result()
            return result
        except Empty:
            raise TimeoutError(f'@GrokAI did not respond in {timeout} seconds')

    def reset_dialog(self) -> None:
        run_coroutine_threadsafe(
            coro=self.__tg.send_message(chat_id='@GrokAI', text='/newchat'),
            loop=self.__tg.loop,
        ).result()
