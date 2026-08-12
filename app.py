from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/report', methods=['GET', 'POST'])
def report_rescue():
    if request.method == 'POST':
        return redirect(url_for('success'))
    return render_template('report.html')

@app.route('/directory')
def directory():
    return render_template('directory.html')

@app.route('/success')
def success():
    return render_template('success.html')

@app.route('/awareness')
def awareness():
    return render_template('awareness.html')

@app.route('/adoption')
def adoption():
    return render_template('adoption.html')

@app.route('/track')
def track():
    return render_template('track.html')

if __name__ == '__main__':
    app.run(debug=True, port=5001)
