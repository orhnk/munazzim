# 0. Günün 1 saatini planlamamak.
Günün 1 saatini program otomatik olarak planlar ve namazları gerekli aralara koyar. Ezan okunur okunmaz program o işi böler ve devamında (eğer iş bitmemişse ya da belirli bir sınırın altında kalmamışsa [^1]) namazdan sonra işi devam ettirir. Abdestleri de program planlar. Nafile namazları kullanıcı kendisi planlar.


# 3. Otomatik Kitap Metadata ekstrasyonu

[^1]: Diyelim ki 1sa kitap okumanın son beş dakikasında ezan okundu, o zaman program sana 55 dk kitap okumayı önerir ve daha sonraki işi 5 dk fazla yapmanı söyler (mesela 5dk fazla ders çalış) çünkü 5dk kitap okumaktan kim bir şey anlar?

# Daha minimal bir proje? إن شاء الله Ana program bir TUI, qalibler de şu şekilde

```
5:00 # Letting the program know when you wake up.
2 My two hours of task
- Complete …
- Do not forget to take notes.
- [5] Study Arabic
.15 …
.5 Take a shower
.40 …
1 …
9:00 15:00 University
.30 …
- Ask the Professor how he finds your project?
.30 …
1 …
1 …
.30
.45
.10
.30
1.30
.30
.30
.30
.20
1.30 Example Task that takes one and a half hour
.15
.15
.30
.15
.10
.10
.3 
.3 
7 Sleep (Nawm)
```

Template'i seçip o günün programını değiştirebiliyorsun. Program, namazları otomatik olarak kendisi yerleştiriyor. Uyuma ve namaz dışında kalan süreyi planlıyorsun. Yapman gereken, ezanı duyunca namaz kılmak ve plana sadık kalmak. 

Bu hem vim, hx ile inşaAllah çalışır hem de günlük planı düzeltmek çok kolay olur. `dd p`.

# Abstract
We as muslims have to pray our salah, read Qur'an-ı Kerîm, read books, study, do do zikr, sports… within a day. But planning is not easy. Especially within a world that is based on UTC sun-based static timezone system. We follow, Hijri taqweem. And normally we have to plan our days, using the salah times.

This project may help you for planning your day relative to the salahs. The main idea is that: Every one of us shall plan our approximately 15.5 hours within a day. 1.5 hours of salah (Already planned by الله تعالى) and 7 hours of sleep. This program allows you to write your days using your favorite editor (helix, neo/vim are recommended) to plan your life. It uses a quick and robust terminal UI [^2].

The swiftness of this program: Normally we have routines for several types of days, Job/School/Uni, Friday and vocations/holidays, weekends and surprises (winter holidays because of bad weather, a day that we woke up late etc.). This program allows you to define qawalib (the term for templates) for each of those conditions. for example, you write a qalib (singulare of qawalib) for a study-heavy-day or a day that you read more than regular, or a day that you listen to some heart-touching waaz. You can really quickly define those. Exactly how you edit a text file using vim.

You may ask, why I'm defining different templates for the same weekdays? The answer I'll try to give is that, (for me,) it's hard to follow the exact copy of the same routine everyday. Changing your daily routine (and you generally have to change it[^3]) may إن شاء الله help you focus more.

So features of this program can be roughly listed like the following:
- Automatic pray-scheduling. Pray right-after you hear azan or got the prayer notification so you get in tact with the plan.
- Plan/replan your day, using plain text and your favorite editor.
- Plan everything. This program doesn't allow you to leave spaces between events, rather you should have your own pause event instead of leaving it blank:
```
5:00
.20 Wudu & Salah
2 Study
.15 Coffie Pause # You have to plan your entire 24 hours.
1 Study
```
- Every action is relative to the other one. So you define what and how lang are you doing as a task, rather than planning using strict time boundaries (This is actually doable via Thabbat events. But not needed).
- Use your own set of qawalib to use it in any day you want and easily switch between different plans.
- Append tasks to your events easily so that you can track how much you have to read or what you should do when studying etc.
- Plan, even the surprises: automatically adjust your time based on the time gone (for ex. surprise friend meeting).
- Sync your plan to Google Callendar and Tasks. This helps you get notifications real-time anywhere.

I used to use bare Google Calendar from the web to plan my days but it lacked features that exist in this program.

# UI
## Main Window
Within the main window the user can edit today's plan that is based on the template. But the changes are not applied to the template rather kept to the day only.
### Your Week
There is a week plan section within the Main Window where the user selects which Qalab (template from the AlQawalib) to use. This is a menu that shows the week plan. So the program shows you every day of the week and you can use vim-like keybindings to traverse the days of the week and change the template on them.

#### Salah Times
Salah times are automatically calculated based on your geolocation using an API (Vakit API fitting Diyanet for Turkey, Aladhan, Pray.Zone etc. configure it from the settings if you don't like the auto-detected option) and put to the plan automatically. There is two point how the salah times are calculated:
The wakeup event has to be before, at least 20 minutes that the fajr. If not, program would raise an error. Also this is only calculated according to today's salah times as in the future, as the suntime shifts, the salah times changes also.
Thabbat times do not get cut by the prayers, for example if I have the following qalib:
```
12:30 13:30 # Dhuhr: 12:50
```
Instead of cutting the event, the program has to put the prayer after the event so here in this example `13:30 …` But if it wasn't a thabbat task program would do:
This
```
…
1 Reading # Let's say Asr is within this boundry
…
```
to this:
```
…
.37 Reading # Let's say Asr is within this boundry
.20 Asr
.23 Reading
…
```

The amount of time that takes you to pray is asked initially when you run the program at first and then configurable via Settings. This is critical because the only time that you are not going to plan is these as the program will plan it itself.
After you enter the durations for each salah, the program will then give you the following information:
```
You have to plan <the calculated duration which is substracted from 24 hours> hours and <…> minutes of your day.
```
and this information is also shown within the statusline like so:
```
TO PLAN: <HH:MM>
```

##### User Defined Prayer Times
Sometimes you would pray within a defined time. E.g you always get up at 5 and pray the Fajr at 5:30. Then you'd use the following keywords (case insensitive)
`Fajr`, `Duhr`, `Asr`, `Maghrib`, `Isha` within your Thabbat task.
```
5:30 5:50 Fajr
9 Gigantic Work
.25 Duhr # Relative Tasks are also supported but are not recommended as Salah in Mosque with Jamaah is always better.
```

Make sure that all prayer times are within the required boundries and make sure that the user is not praying within the Vaqti Kerahat as it's not good to pray those times.
## Settings
Various settings including how much does salawat take as durations,…
## AlQawalib
All of these are a file within `.config`. Just save it with the desired name
This is the template menu.
You can add new templates from the program, it opens your editor within the config/alqawalib/
and you write using the desired format, then you save it as the template name. e.g `:wq my_new_template`. These are not handled by Munazzim but from the editor. Munazzim only calls the $EDITOR variable.

## Shrinker Timer
Let's say you have got a surprise appointment! Do not put your plan away, you can focus on a different surprise task that is not within your plan with the following approach:
press t
and a timer menu will show up, it has got two options, shrinker chronometer and shrinker timer:
### Shrinkers
These two shrinkers are for surprise tasks within your day, start the chronometer and do what you want to do for how long you want it to be. After finishing your surprise task, come and stop the chronometer, now the program knows how much time you did spent for that surprise task that wasn't within your plan. With that information, it's going to put a 'Unplanned Surprise' event for these time boundaries of the day you spent and for the other tasks, it will shrink them and rearrange the prayers. So a user have to spend e.g 36 minutes of Reading instead of 40 mins. Because that he spent %10 percent of his plannable time (24hours - prayers - sleep). The timer works the same. Only that you don't need to go back and stop it rather you define the duration at first.
#### An Exception
Some tasks are not able to get moved, This type of events are called "thabbat". Thabbat events, are defined in the template or the day view like so:
```
09.00 12.00 University Lesson
15.00 16.30 Exam

.10 zikr # Zikr before sleep
21:00 04:00 Sleep
…
```
These tasks are not shrinked, because it's not up to you to attend them or not.
# Add Subtasks easily from both the dayview and the Event-Task relations:

```
4:00
.3  Su
1   Hâsûbî yahut Kitâbî
.30 Kamçılayıcı Okuma (Ansiklopedi)
- [10] -> This is to indicate it will be added to all "Kamçılayıcı Okuma (Ansiklopedi)" within ten days
.30 Mefhum Araştırması
- [7*2] 2. Dünya savaşı # <- Two weeks. Math is allowed inside.
…
…
…
```
So within this example the day counter will reduce day by day so that the user will understand how many days left. Also the notes will persist. For the ones which include math blocks, next day only the calculated number will shown. e.g here 7*2 = 14 and tomorrow it will display 13.

## Subtasks and Event Relation
So let's say you have a plan that you read a histrical book mondays and tuesdays, So you'd have the following template:

```
# This snippet is included within both Friday and Monday.
1.30 Read (Science)
- [16] Read Empire of Cotton
```

So the program will see this `[16]` and will put this task (`Read Empire of Cotton`) not to every weekday but to the days that have the related event: `Read (Science)`. So this event will not end in 16 days but it will end in 8 weeks. Because the user has 2 sessions per week and the 16 denotes the recurrance of the sessions not simply bare days. The important thing is the name of the event, it has to be exactly the same, if you'd have `Read (History)` the program will not attach the task to that event. The user can have multiple of the same event within the same day, so the tasks get attached to it also. If you want the Tasks not to attach to your event session, simply use a different name: `Qur'an Reading`, `Hadith Reading`, `Reading (Science)`, `Reading (Jules Verne)` …

# Example Qalib
## Syntax
```
HH:MM # This is the wakeup event
.5 Brush your teeth and wash your face
.40 40 minutes of a task it means.
.4 this is four minutes.
1.30 this is one and a half hours of work
HH:MM HH:MM This is my Thabbat task.
- [] Do not forget to take notes # if there is no number inside, this means never end this task unless the user deletes it.
1.3 one hour and three minutes
1 one hour
```

> [!NOTE]
> The wakeup event's name is ignored and no such event is synced to the Calendar as it's purpose is to let the program know when the day starts.

> [!IMPORTANT]
> the HH:MM format only uses the 24hours-format. The 12hours format is not supported

# Google Callendar and Google Tasks Sync
Whenever an edit is done, saved and exited (using your favorite editor, helix, vim, neovim or even vsc and emacs etc. program simply calls $EDITOR) the changes are synced to your Google Callendar and Google Tasks (If you have changed your tasks).

## Recurring Tasks and events
All events thus the tasks are recurring weekly. So the program will repeat them accordingly. Using the Google Callendar's API.
### The Algorithm
Define each of the event within the callendar and repeat them once a week. Then define the attached tasks, and repeat them once a week. But to not mix the auto-generated tasks with the user's ones, add a new tasklist using the Tasks API, name it `Munazzim`.

## How the Events are Synced to the Calendar?
First the algorithm places every Thabbat task to the plan, then starts from the wakeup event and generates the required time boundries with the events and tasks, if the algorithm faces a problem, warns the user and opens the qalib with a Warning as a comment:

```
5:00
.5 Wudu
.15 Salah
5:30 Take the Bus to the Uni # ERROR: Expected 5:20, Found 5:30
```
Or too early
```
5:00
.5 Wudu
.15 Salah
5:10 Take the Bus to the Uni # ERROR: Expected 5:20, Found 5:10
```

---
[^2]: This is the most robust way I think. But I guess, many people may give up, instead of learning few keybindings. So a GUI version that runs cross-platform is needed.

[^3]: Because normally let's say, I have to clean my room, not every day but once a week. So I will have that day specially-programmed. Or let's say you may not be taking shower every morning?