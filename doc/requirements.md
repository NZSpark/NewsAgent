1. 增加 news chat 功能，实现与本地用户以及微信bot的对话。
2. 在 news chat 功能中，输入可以来自微信bot，也可以来自本地用户终端。
3. 在 news chat 功能中，输出同时发送给微信bot和本地用户终端。
4. 对于普通输入信息，转发给本地LLM，并将返回信息作为输出信息发送给微信bot和本地用户终端。
5. 支持 / 开始的指令信息，指令为当前news已经实现的功能，例如 /fetch、/pipeline、/recent、/ask、/report 等等。并将返回信息作为输出信息发送给微信bot和本地用户终端。
6. 用 /quit 指令退出 news chat 功能。