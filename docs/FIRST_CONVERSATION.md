# Your First Conversation with LocalPilot

A calm, step-by-step guide for running your first local artificial intelligence model.  
No programming knowledge, machine-learning background, or complex terminal experience required.

---

## Section 1 — Before You Begin

Before installing anything, here is what you need to know:

* **What LocalPilot does**: It tests your computer's memory and processor, helps you select an AI model that fits safely, downloads it, and lets you chat with it.
* **Operating Systems supported**: 
  * **macOS** (Apple Silicon M1, M2, M3, M4, or Intel).
  * **Linux** (Ubuntu, Debian, Fedora, Arch, and most standard distributions).
  * **Windows** is supported through the Windows Subsystem for Linux (WSL2).
* **It runs inside a terminal**: LocalPilot displays a full-screen visual dashboard with menus, buttons, and text inside your terminal window.
* **Internet connection**: You need an active internet connection to download the installer and your chosen model. Once the model is downloaded and verified, everyday chatting works completely offline on your computer.
* **Memory & Storage**:
  * For lightweight models (good for everyday writing and short questions): at least **8 GB of RAM** and **10 GB of free disk space**.
  * For larger models (deeper reasoning and longer documents): **16 GB of RAM** or more is recommended.
* **Prerequisites**: Python 3.11 or newer is required. The installer checks this for you automatically.

---

## Section 2 — Open the Terminal

A **terminal** is a window where you can type commands to your computer instead of clicking desktop icons.

### On macOS
1. Hold down the **Command** key (`⌘`) and press the **Spacebar** to open Spotlight Search.
2. Type `Terminal` and press **Return**.
3. A small window with a prompt will appear.

### On Linux
1. Press **Ctrl + Alt + T** on your keyboard.
2. If that shortcut is not configured on your desktop, open your Application Menu (usually in the bottom-left or top-left corner), search for **Terminal**, and click to open it.

### Understanding the Prompt
When the terminal opens, you will see a line of text ending in a symbol like `$` or `%`. This is the **command prompt**, indicating the terminal is ready:

```text
username@computer:~$ 
```

You do not type the prompt itself. You only copy and paste the commands shown in the boxes below.

---

## Section 3 — Install LocalPilot

To install LocalPilot, copy the following command exactly as written:

```bash
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash
```

### What Happens During Installation
1. The script verifies that your operating system is supported and that Python 3.11+ is present.
2. It creates an isolated application directory:
   * **Linux**: `~/.local/share/localpilot`
   * **macOS**: `~/Library/Application Support/LocalPilot`
3. It installs LocalPilot into its own self-contained environment without modifying your existing files or system software.
4. **No root or administrator passwords are required**.

### Expected Output
When installation finishes, you will see:
```text
============================================================
        LocalPilot successfully installed!                  
============================================================
You can launch LocalPilot by running:
  localpilot
```

*(Note: For developers who prefer installing via Python package tools, `pipx install localpilot` or `pip install localpilot` are also available).*

### Common Installation Problems
* **`curl: command not found`**: Install `curl` using your package manager (for example on Ubuntu/Debian: `sudo apt update && sudo apt install -y curl`).
* **Python version error**: If the installer reports that Python 3.11+ was not found, install Python 3.11 or newer through your system package manager or from [python.org](https://www.python.org/).

---

## Section 4 — Open LocalPilot

To open the visual dashboard, type this command into your terminal and press **Enter**:

```bash
localpilot
```

Your terminal window will transform into a full-screen visual dashboard with buttons, system metrics, and navigation tabs.

If this is your first time launching LocalPilot, it will automatically open the **Guided Setup Wizard**.

---

## Section 5 — Let LocalPilot Check Your Computer

The wizard opens at **Step 1 of 4: Computer Capability Check**.

### Why This Check Matters
Running an artificial intelligence model requires loading gigabytes of data into your computer's short-term memory (RAM). If a model is too large, your computer could become sluggish or unresponsive. LocalPilot calculates a **Safe Memory Budget**—reserving enough memory for your operating system, web browser, and everyday apps.

### What You Will See
* **Operating System & Architecture** (e.g. Linux x86_64 or macOS ARM64).
* **System Memory**: Total RAM, currently available RAM, and the safe budget calculated for models.
* **Processor & Graphics**: Number of processor cores and whether a graphics card (GPU) or neural accelerator was detected.

### What to Do
* Review the verdict text. If your computer is ready, select the blue **Continue →** button (or press **Enter**).
* *(Optional)*: You can click **Computer details** if you want to inspect detailed hardware diagnostics, but it is not required.

---

## Section 6 — Choose What You Want Help With

Next, **Step 2 of 4: What would you like help with?** asks for your goal.

Choose between:
1. **Everyday questions and writing** *(Recommended default)*: Tailored for drafting emails, summarizing notes, brainstorming, and general dialogue.
2. **Coding assistance**: Tailored for programming questions, debugging scripts, and explaining code.
3. **Browse all compatible models**: Explores the complete catalog of models that fit your computer.

Select your option and click **Find Best Models →**.

---

## Section 7 — Choose a Model

At **Step 3 of 4: Recommended Models for Your Computer**, LocalPilot presents a shortlist of models that fit comfortably within your safe memory budget.

### What is an "AI Model"?
A **model** is the downloaded knowledge file that answers your questions. Different models are created by research teams (like Meta, Google DeepMind, Alibaba, and Mistral) with different strengths and sizes.

### What to Compare on the Screen
* **Model Name & Lab**: e.g., `Llama 3.2 3B (Meta)` or `Qwen 2.5 Coder 1.5B (Alibaba)`.
* **Download Size**: How much data will be downloaded over the internet (typically 1.2 GB to 4.5 GB for starter models).
* **Expected Fit**: Indicates that the model fits within your safe memory budget under standard settings.
* **Role**: Badges such as `★ Top Pick`, `Compact Choice`, or `Lightweight Choice`.

Select the model that fits your goal, then click **Review Setup →**.

---

## Section 8 — Review and Approve Setup

At **Step 4 of 4: Review & Approve Setup**, LocalPilot displays a clear summary of what will happen before any file is downloaded:

* **Model Name**: The exact model selected.
* **Download Size**: Disk space needed for the download.
* **Storage Location**: The local folder on your computer where the model will be stored.
* **Safety Confirmation**: Confirms that your personal files, operating system settings, and other applications remain untouched.

### Your Choices
* To proceed: Click **Set Up Model**.
* To change your mind: Click **← Back** or press the **Esc** key to return to model selection. Nothing is downloaded until you confirm.

---

## Section 9 — Wait for Setup and Testing

While LocalPilot configures your model, you will see a live progress checklist:

1. `✓` Computer checked
2. `✓` Supporting software prepared
3. `►` Downloading model weights...
4. `○` Starting model
5. `○` Testing a response

### What is Happening?
* **Download**: The model weights file is downloaded over the internet. Time depends on your internet speed (usually 2 to 10 minutes).
* **Testing a response**: LocalPilot starts the model in the background and asks it a quick test question. This verifies that the model actually runs successfully on your hardware before marking it ready.

### Cancelling Safely
If you need to cancel at any time, click **Cancel Setup** (or press **Esc**). LocalPilot will cleanly stop the process and remove any partial download files so your storage stays clean.

---

## Section 10 — Send Your First Message

When verification passes, the screen displays:  
**🎉 Your model is ready.**

Click **Start Chatting** (or run `localpilot chat` from your terminal).

A chat screen opens. Type your prompt into the input bar and press **Enter**.

### Example Prompts to Try
Copy, paste, or adapt any of these beginner-friendly prompts:

* **Drafting an email**:
  > *"Please help me write a polite email to my landlord asking when our building heating will be turned on for the winter."*
* **Explaining a concept simply**:
  > *"Can you explain how photosynthesis works in three simple bullet points suitable for a 10-year-old?"*
* **Summarizing text**:
  > *"Here is a short paragraph: [paste your text here]. Please summarize the key point in one sentence."*
* **Understanding code**:
  > *"What does this Python line do: `numbers = [x * 2 for x in range(5)]`?"*

### A Friendly Note on Accuracy
Artificial intelligence models generate text based on patterns. They can sometimes state inaccurate facts or suggest flawed code. Always review important information before relying on it.

---

## Section 11 — Finishing or Stopping

When you are done chatting, it is helpful to understand the difference between **leaving the chat** and **stopping the model**:

### Leaving Chat (`/exit`)
Type `/exit` or `/quit` (or press **Esc** in the TUI).  
* **What happens**: The conversation closes, but the model stays loaded in memory in the background. If you open chat again a few minutes later, replies begin immediately without waiting for the model to reload.

### Stopping the Model (`localpilot stop`)
When you want to play a game, edit video, or free up all system memory for other tasks:
* Run `localpilot stop` in your terminal, or press the **Stop** button in the TUI.
* **What happens**: The model process is completely terminated, returning all RAM and GPU memory to your computer.

---

## Section 12 — Removing a Model

If you want to free up hard drive storage, you can remove a model you no longer need:

```bash
localpilot remove "Llama 3.2 3B"
```

* **What happens**: LocalPilot stops any running instance, unregisters the model from your library, and deletes the downloaded weights file from your hard drive.
* **Shared files**: If you downloaded models through external tools like Ollama, LocalPilot unregisters its link without deleting shared external files.

---

## Section 13 — If Something Goes Wrong

Here are quick solutions for common situations:

1. **The download stopped or the internet dropped**:  
   Simply run `localpilot` again. The setup process checks what was already downloaded and resumes safely.
2. **Computer feels slow after running a model**:  
   Run `localpilot stop` to free up system memory immediately. For daily use, select a smaller model (such as a 1.5B or 2B model) using `localpilot discover`.
3. **`localpilot: command not found`**:  
   Your terminal may need to know where local commands live. Add this line to your terminal profile (`~/.bashrc` on Linux or `~/.zshrc` on macOS):
   ```bash
   export PATH="$PATH:$HOME/.local/bin"
   ```
4. **An "Access Key Required" screen appeared**:  
   A few models (such as official Llama 3 or Gemma 2 on Hugging Face) require agreeing to the publisher's terms. You can paste a free, read-only Hugging Face access token (`hf_...`) into the box, or select **← Back** and choose an open model (like Qwen or Mistral) that requires no token.
5. **Need diagnostics**:  
   Run `localpilot support` to generate a safe, privacy-redacted diagnostic bundle you can inspect and share when asking for assistance.

---

## Section 14 — What Next?

Now that your first model is running, you can explore:
* **Discovering more models**: Run `localpilot discover` or open the **Find a Model** tab in the visual dashboard.
* **Starring your favorites**: Mark your go-to models as favorites by pressing **F** in the dashboard or running `localpilot favorite add <alias>`.
* **Connecting to your code editor**: Run `localpilot connect` to link your local model to tools like VS Code (Continue) or OpenCode.
* **Deep dive into architecture**: When you are ready to learn how quantization, memory budgeting, and runtimes work under the hood, read the [Conceptual Guide](CONCEPTUAL_GUIDE.md).
